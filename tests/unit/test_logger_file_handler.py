"""同一进程里多个命名 logger 必须共用一份文件句柄，否则日志轮转会永久失败。

背景：`get_logger("core")` / `get_logger("psd")` … 每个名字都会 `_setup_file_handler()`
一次，各自对着同一个 `logs/live2d.log` 开一个 `RotatingFileHandler`。Windows 上重命名
被另一个句柄占用的文件会抛 `PermissionError [WinError 32]`，于是轮转永远失败：日志停在
10MB 不再滚动，且每条触发轮转的记录都被 logging 的错误处理吞掉（只剩 stderr 噪音）。
实测触发方式就是正常启动分层：`core` 与另一个模块的命名 logger 同时写这一份文件。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

from core.logger import Live2DLogger

MAX_BYTES = 10 * 1024 * 1024


def _file_handlers(logger: Live2DLogger) -> list[RotatingFileHandler]:
    return [h for h in logger._logger.handlers if isinstance(h, RotatingFileHandler)]


def _close_all(*loggers: Live2DLogger) -> None:
    for logger in loggers:
        for handler in list(logger._logger.handlers):
            logger._logger.removeHandler(handler)
            handler.close()


def test_named_loggers_share_one_handler_per_file(tmp_path):
    a = Live2DLogger("share_a", log_dir=str(tmp_path))
    b = Live2DLogger("share_b", log_dir=str(tmp_path))
    try:
        ha, hb = _file_handlers(a), _file_handlers(b)
        assert len(ha) == 1 and len(hb) == 1
        assert ha[0] is hb[0], "同一份日志文件只允许一个 RotatingFileHandler"
    finally:
        _close_all(a, b)


def test_rotation_survives_a_second_open_named_logger(tmp_path):
    """两个命名 logger 都开着时，超限文件仍必须完成轮转。

    空文件不会触发轮转（CPython gh-116263），所以这里先写满 10MB 再 emit。
    修复前：另一个命名 logger 的句柄占着文件，重命名失败 → `.1` 永远不出现。
    """
    log_file = tmp_path / "live2d.log"
    log_file.write_bytes(b"x" * (MAX_BYTES + 1))

    a = Live2DLogger("rot_a", log_dir=str(tmp_path))
    b = Live2DLogger("rot_b", log_dir=str(tmp_path))
    try:
        a.info("open stream a")
        b.info("open stream b")
        a.info("this one crosses the rotation threshold")
        assert (tmp_path / "live2d.log.1").exists(), (
            "轮转失败：另一命名 logger 仍持有同一文件的句柄"
        )
        assert log_file.stat().st_size < MAX_BYTES
    finally:
        _close_all(a, b)


@pytest.fixture(autouse=True)
def _no_leaked_handlers():
    yield
    for name in ("share_a", "share_b", "rot_a", "rot_b"):
        lg = logging.getLogger(f"live2d.{name}")
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()

