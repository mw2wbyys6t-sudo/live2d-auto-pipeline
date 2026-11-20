#!/usr/bin/env python3
"""原生面捕（C++/Rust face_tracker.exe）的 Python 客户端。

与 :class:`mediapipe_tracker.FaceTracker` 保持同一套对外接口
（``start`` / ``stop`` / ``get_blendshapes`` / ``get_head_rotation`` /
``is_running``），因此 ``api_server`` 可以在原生二进制可用时优先用它，
不可用时无感回退到 mediapipe。

协议（JSON Lines，逐行 stdout）::

    → {"cmd": "start", "camera_id": 0, "width": 640, "height": 480}
    ← {"type": "started", ...} / {"type": "error", "message": "..."}
    ← {"type": "frame", "live2d_params": {...}, "blendshapes": {...},
       "head_rotation": {"pitch": ...}, "fps": 30.0}
    → {"cmd": "stop"} / {"cmd": "quit"}

原生进程自己做 EMA 平滑与死区，Python 侧只做参数映射，不重复平滑。
二进制/模型缺失时 :func:`available` 返回 False，调用方回退 mediapipe。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.logger import get_logger

log = get_logger("native_face_tracker")

_ROOT = Path(__file__).resolve().parent.parent.parent
_EXE_NAME = "face_tracker.exe" if os.name == "nt" else "face_tracker"

# 启动时等待 started/error 的上限（与 api_server 的 TRACKING_START_TIMEOUT 相互独立）
_START_TIMEOUT = 8.0
# 首帧保留时长：超过这个时间没有新帧就认为追踪已停止（摄像头被拔等）
_FRAME_STALE_S = 2.0


def find_binary() -> Optional[Path]:
    """按部署布局 → 开发布局顺序查找面捕可执行文件。"""
    env = os.environ.get("LIVE2D_FACE_TRACKER")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    candidates = [
        # 开发布局：C++ 版优先（478 点 + 虹膜）
        _ROOT / "native" / "face_tracker_cpp" / "build" / _EXE_NAME,
        # Rust 版回退
        _ROOT / "native" / "face_tracker" / "target" / "release" / _EXE_NAME,
        # 部署布局：与 Python 可执行文件同目录
        Path(sys.executable).resolve().parent / _EXE_NAME,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def find_models_dir() -> Optional[Path]:
    """YuNet + 478 点模型的目录（模型缺失时原生进程只能跑 5 点回退）。"""
    for d in (_ROOT / "models" / "face", _ROOT / "models"):
        if (d / "face_detection_yunet_2023mar.onnx").is_file():
            return d
    return None


def available() -> bool:
    """原生面捕是否可用（二进制 + 模型都在）。"""
    return find_binary() is not None and find_models_dir() is not None


class NativeFaceTracker:
    """把原生 face_tracker 子进程包装成与 mediapipe FaceTracker 同形的对象。"""

    def __init__(self, camera_id: int = 0, width: int = 640, height: int = 480,
                 binary: Optional[Path] = None) -> None:
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.binary = Path(binary) if binary else find_binary()
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
        self._blendshapes: Dict[str, float] = {}
        self._head_rotation: Dict[str, float] = {}
        self._live2d_params: Dict[str, float] = {}
        self._last_frame_at = 0.0
        self._started_at = 0.0
        self._error = ""
        self._fps = 0.0
        self._started = threading.Event()
        self._start_error = ""
        self._stopping = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.binary is None:
            raise RuntimeError(
                "未找到 face_tracker 可执行文件，请先编译 native/face_tracker_cpp/build.bat"
            )
        # 允许 stop → start 重开（与 C++ 版「stop 后可重开摄像头」保持一致）
        self._started.clear()
        with self._lock:
            self._start_error = ""
            self._error = ""
            self._stopping = False
            self._last_frame_at = 0.0
        env = os.environ.copy()
        models = find_models_dir()
        if models is not None:
            env["FACE_TRACKER_MODEL_DIR"] = str(models)
        # stdin/stdout 是协议管道；stderr 直通父进程日志，便于排查模型/摄像头错误
        self._proc = subprocess.Popen(
            [str(self.binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        self._reader = threading.Thread(target=self._read_loop, name="native-face-tracker", daemon=True)
        self._reader.start()
        self._send({"cmd": "start", "camera_id": self.camera_id,
                    "width": self.width, "height": self.height})
        if not self._started.wait(_START_TIMEOUT):
            self.stop()
            raise RuntimeError("面捕进程启动超时（未收到 started 消息）")
        if self._start_error:
            self.stop()
            raise RuntimeError(self._start_error)
        with self._lock:
            self._running = True
            self._started_at = time.monotonic()

    def stop(self) -> None:
        proc = self._proc
        with self._lock:
            self._running = False
            self._stopping = True
        if proc is None:
            return
        try:
            self._send({"cmd": "stop"}, check=False)
            self._send({"cmd": "quit"}, check=False)
        except Exception:
            pass
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proc = None
        with self._lock:
            self._blendshapes = {}
            self._head_rotation = {}
            self._live2d_params = {}

    def is_running(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            now = time.monotonic()
            if self._last_frame_at == 0.0:
                # 刚启动、首帧还没到：给一个启动宽限期，否则调用方会误判失败
                return (now - self._started_at) < _FRAME_STALE_S
            return (now - self._last_frame_at) < _FRAME_STALE_S

    # ------------------------------------------------------------------
    # 帧数据（与 mediapipe FaceTracker 同名同义）
    # ------------------------------------------------------------------

    def get_blendshapes(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._blendshapes)

    def get_head_rotation(self) -> Optional[Dict[str, float]]:
        with self._lock:
            return dict(self._head_rotation) if self._head_rotation else None

    def get_live2d_params(self) -> Dict[str, float]:
        """原生进程已映射好的 Live2D 参数（含平滑与死区）。"""
        with self._lock:
            return dict(self._live2d_params)

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def error(self) -> str:
        return self._error

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _send(self, msg: Dict[str, Any], check: bool = True) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            if check:
                raise RuntimeError("面捕进程未启动")
            return
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            if check:
                raise

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug(f"面捕非 JSON 输出: {line[:200]}")
                continue
            kind = msg.get("type")
            if kind == "ready":
                if not msg.get("has_model"):
                    self._start_error = "面捕模型未加载（缺少 YuNet/onnxruntime.dll）"
                continue
            if kind == "started":
                self._started.set()
                continue
            if kind == "error":
                self._error = str(msg.get("message", "面捕错误"))
                log.warning(f"面捕错误: {self._error}")
                if not self._started.is_set():
                    self._start_error = self._error
                    self._started.set()
                continue
            if kind == "stopped":
                with self._lock:
                    self._running = False
                continue
            if kind != "frame":
                continue
            with self._lock:
                self._last_frame_at = time.monotonic()
                self._fps = float(msg.get("fps", 0.0))
                self._live2d_params = dict(msg.get("live2d_params") or {})
                # 无脸帧不带 blendshapes/head_rotation：清空让上层回落到待机
                self._blendshapes = dict(msg.get("blendshapes") or {})
                self._head_rotation = dict(msg.get("head_rotation") or {})
        with self._lock:
            self._running = False
            # 非主动 stop 的退出（进程崩溃/被杀）要让上层能报出原因，
            # 否则只是画面静默冻结在最后一帧。
            if not self._stopping and not self._error:
                self._error = "面捕进程已退出"


__all__ = ["NativeFaceTracker", "available", "find_binary", "find_models_dir"]
