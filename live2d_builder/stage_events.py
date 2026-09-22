#!/usr/bin/env python3
"""构建阶段事件外发：把 ``log.step`` 的行边界变成结构化进度事件。

背景：Go 侧 ``runInlinePythonTimeout`` 用 ``CombinedOutput()``，构建期间前端
只能等到最终响应。本模块让 pipeline 现有的 ``log.step(n, total, msg)`` 顺带
产出**行分隔 JSON**，Go 桥接逐行读到后就转发给 WebSocket Hub —— 不引入任务
队列，也不改任何返回值。

线格式（与 ``drivers/live2d_runtime/verify.py`` 的 ``LIVE2D_VERIFICATION=``
同一风格）::

    LIVE2D_STAGE={"job_id":"...","step":9,"total":10,...}

只写 stderr：stdout 的最后一行是给 API 的结果 JSON，不能被污染（Go 两侧都会读，
合并后仍然是同一份日志）。

诚实性规则（本仓库的「禁止虚假成功」）：
* 只在**真的观察到**步骤时才有事件；从未上报的阶段由 Go 侧补 ``not_reported``，
  本模块绝不合成 ``completed``。
* 步骤边界只说 ``advanced``（控制流走到了下一步），不代表该阶段产物已验收；
  ``verified`` 恒为 ``false``。唯一的 ``succeeded`` 来自 Go 侧读到官方 Cubism
  Core 的 ``runtime_ready`` 之后。
* 某阶段开放期间若出现 ERROR 级日志，收尾状态变成
  ``advanced_with_errors`` —— 「moc3 编译失败但构建继续往下走」不能被读成成功。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, TextIO

# ----------------------------------------------------------------------
# 线格式
# ----------------------------------------------------------------------

MARKER = "LIVE2D_STAGE="

STATUS_STARTED = "started"
STATUS_ADVANCED = "advanced"
STATUS_ADVANCED_WITH_ERRORS = "advanced_with_errors"
STATUS_FAILED = "failed"
STATUS_NOT_REPORTED = "not_reported"
STATUS_SUCCEEDED = "succeeded"
STATUS_BLOCKED = "blocked"

#: ``core.logger.Live2DLogger.step`` 的固定形状：``[n/total] msg``
_STEP_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+?)\s*$")

#: 终端事件之外，阶段边界最多到 99%：100% 只属于经过验收的 succeeded。
MAX_STAGE_PERCENT = 99

__all__ = [
    "MARKER",
    "STATUS_STARTED",
    "STATUS_ADVANCED",
    "STATUS_ADVANCED_WITH_ERRORS",
    "STATUS_FAILED",
    "STATUS_NOT_REPORTED",
    "STATUS_SUCCEEDED",
    "STATUS_BLOCKED",
    "StageReporter",
    "attach",
    "close_open",
    "detach",
    "emit",
    "encode",
    "parse_line",
    "percent_for",
    "reporter_for",
]


def encode(event: Dict[str, Any]) -> str:
    """事件 dict -> 一行线格式（不含换行）。"""
    return MARKER + json.dumps(event, ensure_ascii=False, default=str)


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """解析一行输出；不是阶段事件就返回 ``None``。"""
    text = (line or "").strip()
    if not text.startswith(MARKER):
        return None
    try:
        payload = json.loads(text[len(MARKER):])
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def emit(event: Dict[str, Any], stream: Optional[TextIO] = None) -> Optional[str]:
    """写一行阶段事件并立刻 flush（进度要实时，不能等缓冲区）。

    进度上报是旁路能力，永远不允许它把构建搞崩：流不可用/编码失败时静默降级
    为「没有事件」，调用方（Go）会因此收到 ``not_reported`` 而不是假成功。
    """
    line = encode(event)
    handle = stream if stream is not None else sys.stderr
    try:
        handle.write(line + "\n")
        handle.flush()
    except Exception:  # noqa: BLE001 - 进度通道坏掉不能影响构建
        return None
    return line


def percent_for(step: int, total: int, entered: bool = False) -> int:
    """阶段百分比。``entered=False`` 表示该阶段刚做完边界推进。"""
    if total <= 0:
        return 0
    done = max(0, step - 1) if entered else max(0, step)
    return min(MAX_STAGE_PERCENT, int(done * 100 / total))


# ----------------------------------------------------------------------
# 上报器
# ----------------------------------------------------------------------

class StageReporter:
    """把「第 n 步开始 / 上一步推进」翻译成事件流。

    事件同时投递给**所有**登记的 sink 以及 ``stream``；一个阶段边界只产生一条
    事件（挂钩本身幂等），所以多个 sink 不会互相复制出重复阶段。
    """

    def __init__(
        self,
        job_id: Optional[str] = None,
        stream: Optional[TextIO] = None,
        sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.job_id = job_id or os.environ.get("LIVE2D_STAGE_JOB") or "inline"
        self._stream = stream
        self._sinks: List[Callable[[Dict[str, Any]], None]] = (
            [sink] if sink is not None else [])
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._open: Optional[Dict[str, Any]] = None
        self._open_errors: List[str] = []
        self._index = 0
        self.total = 0
        #: 已发布事件（供调用方复核：谁都没法伪造这里没有的东西）
        self.events: List[Dict[str, Any]] = []

    def add_sink(self, sink: Callable[[Dict[str, Any]], None]) -> None:
        """追加一个订阅者。

        幂等 attach 不能悄悄把后来者的 sink 扔掉 —— 那会让调用方以为自己已经
        接上了，却一条事件都收不到。
        """
        if sink is None:
            return
        with self._lock:
            if sink not in self._sinks:
                self._sinks.append(sink)

    # -- 低层 ------------------------------------------------------
    def _publish(self, **fields: Any) -> Dict[str, Any]:
        self._index += 1
        event: Dict[str, Any] = {
            "job_id": self.job_id,
            "index": self._index,
            "step": 0,
            "total": self.total,
            "name": "",
            "status": STATUS_STARTED,
            "percent": 0,
            "elapsed_ms": int((self._clock() - self._started) * 1000),
            "verified": False,
        }
        event.update(fields)
        self.events.append(event)
        for sink in list(self._sinks):
            try:
                sink(dict(event))
            except Exception:  # noqa: BLE001 - 订阅者坏掉不能拖垮构建
                pass
        emit(event, self._stream)
        return event

    # -- 观察 ------------------------------------------------------
    def observe_step(self, step: int, total: int, name: str) -> Optional[Dict[str, Any]]:
        """记录一个新步骤：先把上一个开放步骤推进，再宣布自己开始了。"""
        with self._lock:
            if total > self.total:
                self.total = int(total)
            self._close_open_locked(STATUS_ADVANCED)
            self._open = {"step": int(step), "total": int(total), "name": str(name)}
            return self._publish(
                step=int(step),
                total=int(total),
                name=str(name),
                status=STATUS_STARTED,
                percent=percent_for(int(step), int(total), entered=True),
            )

    def note_error(self, message: str) -> None:
        """某阶段开放期间报了错——推进时不能只说 advanced。"""
        with self._lock:
            if self._open is None:
                return
            text = (message or "").strip()
            if text and text not in self._open_errors:
                self._open_errors.append(text)

    def close_open(
        self, status: str = STATUS_ADVANCED, message: str = ""
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._close_open_locked(status, message)

    def _close_open_locked(self, status: str, message: str = "") -> Optional[Dict[str, Any]]:
        open_step, errors = self._open, self._open_errors
        self._open, self._open_errors = None, []
        if not open_step:
            return None
        final_status = status
        note = message
        # 只有「自然推进」才可能被错误日志改写成降级状态；failed 保持它的诚实。
        if status == STATUS_ADVANCED and errors:
            final_status = STATUS_ADVANCED_WITH_ERRORS
            note = "; ".join(errors[:3]) if not note else note
        elif note and errors:
            note = f"{note}; {'; '.join(errors[:3])}"
        return self._publish(
            step=open_step["step"],
            total=open_step["total"],
            name=open_step["name"],
            status=final_status,
            message=note,
            percent=percent_for(open_step["step"], open_step["total"]),
        )

    @property
    def open_step(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._open) if self._open else None


# ----------------------------------------------------------------------
# logging 挂钩
# ----------------------------------------------------------------------

_HANDLERS: Dict[str, "_StageLogHandler"] = {}
_HANDLERS_LOCK = threading.Lock()


class _StageLogHandler(logging.Handler):
    """挂在 ``live2d`` 这个祖先 logger 上：``log.step`` 的每条 INFO 都会经过。"""

    def __init__(self, reporter: StageReporter, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self.reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        match = _STEP_RE.match(message.strip())
        if match:
            self.reporter.observe_step(
                int(match.group(1)), int(match.group(2)), match.group(3)
            )
            return
        if record.levelno >= logging.ERROR:
            self.reporter.note_error(message)


def attach(
    job_id: Optional[str] = None,
    stream: Optional[TextIO] = None,
    sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    logger_name: str = "live2d",
    level: int = logging.INFO,
) -> StageReporter:
    """安装阶段事件挂钩；同一 ``logger_name`` 重复调用是幂等的。

    幂等很重要：Go 的内联片段每次都会 ``attach``，若重复挂 handler 就会出现
    同一阶段多条事件，前端会把它读成「阶段重跑」。
    """
    with _HANDLERS_LOCK:
        existing = _HANDLERS.get(logger_name)
        if existing is not None:
            # 幂等：不重复挂 handler（否则同一阶段会被报两次），但后来者的
            # sink 仍要接上，不能让它以为自己订阅成功了。
            existing.reporter.add_sink(sink)
            return existing.reporter
        parent = logging.getLogger(logger_name)
        # 刻意不改 parent.level：记录能否到达由**发起方** logger 的级别决定，
        # 祖先 handler 只做 handler 自己的过滤。动它等于改全局日志行为。
        reporter = StageReporter(job_id=job_id, stream=stream, sink=sink)
        handler = _StageLogHandler(reporter, level=level)
        parent.addHandler(handler)
        _HANDLERS[logger_name] = handler
        return reporter


def detach(logger_name: str = "live2d") -> bool:
    """拆掉挂钩（测试收尾用）。没有挂钩时返回 ``False``。"""
    with _HANDLERS_LOCK:
        handler = _HANDLERS.pop(logger_name, None)
    if handler is None:
        return False
    logging.getLogger(logger_name).removeHandler(handler)
    handler.close()
    return True


def reporter_for(logger_name: str = "live2d") -> Optional[StageReporter]:
    with _HANDLERS_LOCK:
        handler = _HANDLERS.get(logger_name)
    return handler.reporter if handler else None


def close_open(
    status: str = STATUS_ADVANCED,
    message: str = "",
    logger_name: str = "live2d",
) -> Optional[Dict[str, Any]]:
    """关闭仍开放的阶段（构建正常返回后由内联片段调用）。"""
    reporter = reporter_for(logger_name)
    if reporter is None:
        return None
    return reporter.close_open(status=status, message=message)


if __name__ == "__main__":  # pragma: no cover - 手工冒烟
    rep = attach(job_id="smoke", sink=lambda evt: None)
    logging.getLogger("live2d.rigging.smoke").info("[1/2] 示例阶段")
    logging.getLogger("live2d.rigging.smoke").info("[2/2] 第二个阶段")
    close_open()
    print(json.dumps(rep.events, ensure_ascii=False, indent=2))
    detach()
