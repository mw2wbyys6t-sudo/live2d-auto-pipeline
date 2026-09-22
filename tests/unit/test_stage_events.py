"""live2d_builder.stage_events：把 log.step 边界变成行分隔 JSON 进度事件。

这些测试盯的是**诚实性**：没有真的观察到步骤就不许有事件，阶段边界不许带
验收语义；以及和 Go 桥接的线格式配对（``LIVE2D_STAGE=``）。
"""

import io
import json
import sys

import pytest

from core.logger import get_logger
from live2d_builder import stage_events


MARKER = "LIVE2D_STAGE="


@pytest.fixture(autouse=True)
def detached_handlers():
    """挂钩挂在祖先 logger 上，必须每次清干净，否则测试之间会互相串事件。"""
    stage_events.detach()
    yield
    stage_events.detach()


def attach_collector(job_id="job-x", stream=None):
    events = []
    reporter = stage_events.attach(job_id=job_id, stream=stream, sink=events.append)
    return reporter, events


def pipeline_log():
    """用真实的项目 logger：它决定 [n/total] 的字面格式，正则必须跟得上。"""
    return get_logger("rigging.stage_events_test")


# ---------------------------------------------------------------- 线格式

def test_marker_is_the_shared_wire_contract():
    assert stage_events.MARKER == MARKER
    event = {"job_id": "j", "step": 9, "total": 10, "status": "started"}
    line = stage_events.encode(event)
    assert line.startswith(MARKER)
    assert stage_events.parse_line(line) == event


def test_parse_line_ignores_noise_and_truncated_payloads():
    assert stage_events.parse_line("[9/10] Compiling moc3") is None
    assert stage_events.parse_line("") is None
    assert stage_events.parse_line(MARKER + "{not json") is None
    assert stage_events.parse_line(MARKER + '["array"]') is None


def test_emit_writes_a_single_flushed_line():
    stream = io.StringIO()
    writes = {"flushes": 0}
    original_flush = stream.flush

    def counting_flush():
        writes["flushes"] += 1
        original_flush()

    stream.flush = counting_flush
    line = stage_events.emit({"step": 1, "status": "started"}, stream)
    assert line is not None
    assert stream.getvalue() == line + "\n"
    # 进度要实时：不 flush 就等于事件在进程退出时才一股脑到齐。
    assert writes["flushes"] == 1


def test_emit_never_breaks_the_build_when_the_pipe_is_gone():
    class Broken:
        def write(self, _data):
            raise OSError("管道已关闭")

        def flush(self):
            raise OSError("管道已关闭")

    assert stage_events.emit({"step": 1, "status": "started"}, Broken()) is None


def test_emit_defaults_to_stderr_not_stdout(monkeypatch):
    """stdout 的最后一行是给 API 的结果 JSON，进度不能污染它。"""
    capture = io.StringIO()
    monkeypatch.setattr(sys, "stderr", capture)
    stage_events.emit({"step": 1, "status": "started"})
    assert capture.getvalue().startswith(MARKER)


def test_percent_never_reaches_100_at_stage_boundaries():
    assert stage_events.percent_for(1, 10, entered=True) == 0
    assert stage_events.percent_for(3, 4, entered=True) == 50
    assert stage_events.percent_for(10, 10) == stage_events.MAX_STAGE_PERCENT == 99
    assert stage_events.percent_for(2, 0) == 0


# ------------------------------------------------------- 与 log.step 的对接

def test_step_logging_emits_started_then_advanced_boundaries():
    _, events = attach_collector()
    log = pipeline_log()
    log.step(1, 3, "Generating meshes")
    log.step(2, 3, "Laying out UVs")
    log.step(3, 3, "Baking texture atlases")
    stage_events.close_open()

    sequence = [(e["step"], e["status"]) for e in events]
    assert sequence == [
        (1, "started"), (1, "advanced"),
        (2, "started"), (2, "advanced"),
        (3, "started"), (3, "advanced"),
    ]
    names = {e["step"]: e["name"] for e in events}
    assert names == {
        1: "Generating meshes",
        2: "Laying out UVs",
        3: "Baking texture atlases",
    }
    assert all(e["job_id"] == "job-x" for e in events)
    assert all(e["total"] == 3 for e in events)


def test_last_step_is_only_closed_by_an_explicit_close_open():
    """构建还在跑时，最后一步必须停在 started —— 不能被谁顺手「补完」。"""
    _, events = attach_collector()
    pipeline_log().step(1, 2, "Exporting model3.json")
    assert [(e["step"], e["status"]) for e in events] == [(1, "started")]
    stage_events.close_open()
    assert [(e["step"], e["status"]) for e in events] == [
        (1, "started"), (1, "advanced"),
    ]


def test_non_step_logging_produces_no_events():
    _, events = attach_collector()
    log = pipeline_log()
    log.info("Parameters: 30 defined")
    log.section("Build complete")
    log.success("Built 'hero' in 12.4s")
    log.debug("[1/3] 调试里的假步骤也不算")
    assert events == []


def test_close_open_returns_none_when_nothing_is_open():
    _, events = attach_collector()
    assert stage_events.close_open() is None
    assert events == []


def test_error_inside_a_step_cannot_be_reported_as_a_clean_advance():
    """moc3 编译失败后 pipeline 会继续往下跑；这一步不能显示成正常走完。"""
    _, events = attach_collector()
    log = pipeline_log()
    log.step(9, 10, "Compiling moc3")
    log.error("moc3 编译失败: rotation 变形器未编译")
    log.step(10, 10, "Validating model")

    closed = [e for e in events if e["step"] == 9 and e["status"] != "started"]
    assert len(closed) == 1, events
    assert closed[0]["status"] == stage_events.STATUS_ADVANCED_WITH_ERRORS
    assert "rotation 变形器" in closed[0]["message"]
    # 降级之后的收尾也不能被读成成功。
    assert closed[0]["verified"] is False


def test_failed_close_open_keeps_the_error_and_never_blends_into_advance():
    _, events = attach_collector()
    pipeline_log().step(7, 10, "Building physics")
    stage_events.close_open(stage_events.STATUS_FAILED, "子进程被掐掉")
    last = events[-1]
    assert last["status"] == stage_events.STATUS_FAILED
    assert last["message"] == "子进程被掐掉"
    assert last["verified"] is False


def test_every_emitted_stage_event_is_unverified():
    """verified=true 属于官方内核验收，Python 这边一律给 false。"""
    _, events = attach_collector()
    log = pipeline_log()
    log.step(1, 2, "Generating meshes")
    log.step(2, 2, "Laying out UVs")
    stage_events.close_open()
    assert events and all(e["verified"] is False for e in events)
    assert all(e["percent"] <= stage_events.MAX_STAGE_PERCENT for e in events)


# ------------------------------------------------------------------ 挂钩本身

def test_attach_is_idempotent_so_a_stage_is_not_reported_twice():
    first, events_a = attach_collector()
    second, events_b = attach_collector()
    assert first is second
    pipeline_log().step(1, 2, "Generating meshes")
    assert len(events_a) == 1
    assert events_a is not events_b  # 两个 sink 各收一次，但只有一个 handler
    assert len(events_b) == 1
    assert [e["index"] for e in events_a] == [1]


def test_detach_stops_the_stream():
    attach_collector()
    stage_events.detach()
    _, events = attach_collector(job_id="after")
    # 重新 attach 才恢复；同时确认 index 从新 reporter 重新开始。
    pipeline_log().step(1, 3, "Generating meshes")
    assert [e["job_id"] for e in events] == ["after"]
    assert events[0]["index"] == 1
    assert stage_events.detach() is True
    assert stage_events.detach() is False


def test_events_reach_the_reporter_stream_as_json_lines():
    stream = io.StringIO()
    stage_events.attach(job_id="streamed", stream=stream)
    log = pipeline_log()
    log.step(1, 2, "Generating meshes")
    log.step(2, 2, "Laying out UVs")
    lines = [ln for ln in stream.getvalue().splitlines() if ln]
    assert len(lines) == 3  # started1, advanced1, started2
    assert all(ln.startswith(MARKER) for ln in lines)
    payload = json.loads(lines[0][len(MARKER):])
    assert payload["name"] == "Generating meshes"
    assert payload["job_id"] == "streamed"


def test_reporter_for_exposes_observed_events_only():
    reporter, _ = attach_collector()
    assert stage_events.reporter_for() is reporter
    pipeline_log().step(1, 4, "Generating meshes")
    assert reporter.open_step == {"step": 1, "total": 4, "name": "Generating meshes"}
    assert [e["status"] for e in reporter.events] == ["started"]
