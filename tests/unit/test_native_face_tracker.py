"""原生面捕客户端（drivers/face_tracker/native_tracker.py）的契约测试。

不依赖摄像头与真实 exe：用假的 Popen 喂 JSON Lines，验证
1) 二进制/模型查找顺序；2) 帧字段落到同形 getter；3) 错误消息进入 error。
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from drivers.face_tracker import native_tracker as nt


class _FakeProc:
    """最小 Popen 替身：只提供 stdout 迭代与 stdin 写入。"""

    def __init__(self, lines):
        self.stdout = io.StringIO("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
        self.stdin = io.StringIO()

    def wait(self, timeout=None):
        return 0


def test_find_binary_prefers_cpp_build_layout(tmp_path, monkeypatch):
    cpp = tmp_path / "native" / "face_tracker_cpp" / "build"
    rust = tmp_path / "native" / "face_tracker" / "target" / "release"
    cpp.mkdir(parents=True)
    rust.mkdir(parents=True)
    exe = "face_tracker.exe" if __import__("os").name == "nt" else "face_tracker"
    (cpp / exe).write_bytes(b"")
    (rust / exe).write_bytes(b"")
    monkeypatch.setattr(nt, "_ROOT", tmp_path)
    monkeypatch.setattr(nt, "_EXE_NAME", exe)
    monkeypatch.delenv("LIVE2D_FACE_TRACKER", raising=False)
    assert nt.find_binary() == cpp / exe


def test_find_binary_falls_back_to_rust_layout(tmp_path, monkeypatch):
    exe = "face_tracker.exe" if __import__("os").name == "nt" else "face_tracker"
    rust = tmp_path / "native" / "face_tracker" / "target" / "release"
    rust.mkdir(parents=True)
    (rust / exe).write_bytes(b"")
    monkeypatch.setattr(nt, "_ROOT", tmp_path)
    monkeypatch.setattr(nt, "_EXE_NAME", exe)
    monkeypatch.delenv("LIVE2D_FACE_TRACKER", raising=False)
    assert nt.find_binary() == rust / exe


def test_available_requires_binary_and_models(tmp_path, monkeypatch):
    exe = "face_tracker.exe" if __import__("os").name == "nt" else "face_tracker"
    build = tmp_path / "native" / "face_tracker_cpp" / "build"
    build.mkdir(parents=True)
    (build / exe).write_bytes(b"")
    monkeypatch.setattr(nt, "_ROOT", tmp_path)
    monkeypatch.setattr(nt, "_EXE_NAME", exe)
    monkeypatch.delenv("LIVE2D_FACE_TRACKER", raising=False)
    assert nt.available() is False  # 只有 exe，没有模型

    face = tmp_path / "models" / "face"
    face.mkdir(parents=True)
    (face / "face_detection_yunet_2023mar.onnx").write_bytes(b"")
    assert nt.available() is True


def test_frames_populate_getters_and_stop_on_clean_exit():
    tracker = nt.NativeFaceTracker(binary=Path("dummy"))
    tracker._proc = _FakeProc([
        {"type": "ready", "has_camera": True, "has_model": True},
        {"type": "started", "camera_id": 0, "width": 640, "height": 480},
        {"type": "frame", "fps": 29.5,
         "live2d_params": {"ParamAngleX": 12.5, "ParamMouthOpenY": 0.4},
         "blendshapes": {"jawOpen": 0.4},
         "head_rotation": {"pitch": -3.0, "yaw": 12.5, "roll": 1.0}},
    ])
    tracker._started.set()
    tracker._running = True
    tracker._read_loop()

    # 读循环结束（进程 stdout 关闭）→ 运行态归零
    assert tracker.is_running() is False
    assert tracker.get_live2d_params()["ParamAngleX"] == pytest.approx(12.5)
    assert tracker.get_blendshapes()["jawOpen"] == pytest.approx(0.4)
    assert tracker.get_head_rotation()["yaw"] == pytest.approx(12.5)
    assert tracker.fps == pytest.approx(29.5)


def test_frame_without_face_clears_previous_pose():
    tracker = nt.NativeFaceTracker(binary=Path("dummy"))
    tracker._proc = _FakeProc([
        {"type": "started"},
        {"type": "frame", "live2d_params": {"ParamAngleX": 9.0},
         "blendshapes": {"jawOpen": 0.5},
         "head_rotation": {"pitch": 0.0, "yaw": 9.0, "roll": 0.0}},
        {"type": "frame", "live2d_params": {"ParamAngleX": 0.0}},
    ])
    tracker._started.set()
    tracker._running = True
    tracker._read_loop()
    # 无脸帧不带 blendshapes/head_rotation → getter 回落为空，供上层切待机
    assert tracker.get_blendshapes() == {}
    assert tracker.get_head_rotation() is None


def test_error_message_is_surfaced():
    tracker = nt.NativeFaceTracker(binary=Path("dummy"))
    tracker._proc = _FakeProc([
        {"type": "ready", "has_model": True},
        {"type": "error", "message": "摄像头被占用"},
    ])
    tracker._read_loop()
    assert "摄像头被占用" in tracker.error
    # 未 started 就报错 → 启动流程能拿到失败原因
    assert "摄像头被占用" in tracker._start_error


def test_unexpected_exit_sets_error_but_manual_stop_does_not():
    crashed = nt.NativeFaceTracker(binary=Path("dummy"))
    crashed._proc = _FakeProc([{"type": "started"}])
    crashed._read_loop()
    assert "已退出" in crashed.error

    stopped = nt.NativeFaceTracker(binary=Path("dummy"))
    stopped._proc = _FakeProc([{"type": "started"}])
    stopped._stopping = True
    stopped._read_loop()
    assert stopped.error == ""

