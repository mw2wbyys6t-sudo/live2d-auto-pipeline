"""native/split_kernel（Rust）加速内核的 ctypes 加载器。

边界约定（不乱破坏原有项目）：
* 内核只接管**纯函数热点**（首批：alpha_over 图层叠加）。Python 保留全部
  编排、启发式与模型推理；每个接入点都必须先过逐字节对拍
  （tests/unit/test_native_kernel.py）。
* DLL 缺失、加载失败、ABI 不符、或 ``LIVE2D_NATIVE_KERNEL=0`` 时一律
  返回 None，调用方回退原 Python 实现——原生内核永远是**可选加速器**，
  不是新的依赖。
* ``LIVE2D_SPLIT_KERNEL`` 可指定 DLL 路径（部署布局与开发布局不同）。
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import List, Optional

import numpy as np

from core.logger import get_logger

log = get_logger("native_kernel")

_ABI_VERSION = 1
_lib: Optional[ctypes.CDLL] = None
_load_attempted = False


def _candidate_paths() -> List[Path]:
    env = os.environ.get("LIVE2D_SPLIT_KERNEL")
    if env:
        return [Path(env)]
    root = Path(__file__).resolve().parent.parent
    return [
        root / "native" / "split_kernel" / "target" / "release" / "split_kernel.dll",
        root / "native" / "split_kernel" / "target" / "release" / "libsplit_kernel.so",
        root / "bin" / "split_kernel.dll",
    ]


def _load() -> Optional[ctypes.CDLL]:
    global _lib, _load_attempted
    if _load_attempted:
        return _lib
    _load_attempted = True
    if os.environ.get("LIVE2D_NATIVE_KERNEL", "1") == "0":
        log.info("原生内核已被 LIVE2D_NATIVE_KERNEL=0 关闭")
        return None
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.sk_abi_version.restype = ctypes.c_uint32
            if lib.sk_abi_version() != _ABI_VERSION:
                log.warning(f"原生内核 ABI 不符（{path}），回退 Python 实现")
                continue
            lib.sk_alpha_over.argtypes = [
                ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t,
                ctypes.c_size_t, ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            lib.sk_alpha_over.restype = ctypes.c_int32
            lib.sk_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            lib.sk_free.restype = None
            _lib = lib
            log.info(f"原生拆分内核已加载: {path}")
            return _lib
        except OSError as exc:
            log.warning(f"原生内核加载失败（{path}）: {exc}")
    return None


def available() -> bool:
    """内核是否可用（加载器只尝试一次，之后走缓存）。"""
    return _load() is not None


def alpha_over(stack: List[Optional[np.ndarray]], h: int, w: int) -> Optional[np.ndarray]:
    """Rust 版图层叠加；不可用/空栈/形状不符时返回 None 让调用方回退。

    与 ``LayerComposer._alpha_over`` 的 float64 公式逐字节一致
    （见 native/split_kernel/src/blend.rs 的推导注释）。
    """
    lib = _load()
    if lib is None:
        return None
    arrays = []
    for a in stack:
        if a is None:
            continue
        if a.shape != (h, w, 4) or a.dtype != np.uint8:
            return None
        arrays.append(np.ascontiguousarray(a))
    if not arrays:
        return None
    n = len(arrays)
    ptrs = (ctypes.c_void_p * n)(*[a.ctypes.data for a in arrays])
    out = ctypes.c_void_p()
    rc = lib.sk_alpha_over(ptrs, n, h, w, ctypes.byref(out))
    if rc != 0 or not out.value:
        return None
    size = h * w * 4
    try:
        buf = (ctypes.c_ubyte * size).from_address(out.value)
        result = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4).copy()
    finally:
        lib.sk_free(out, size)
    return result
