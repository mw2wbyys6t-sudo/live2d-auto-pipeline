"""Rust 拆分内核与 Python 参考实现的逐字节对拍。

原则（native/split_kernel 的边界）：对拍不过就不接线。DLL 未构建时这些
用例跳过（内核是可选加速器，不是依赖）；构建后必须逐字节相等，
不允许"近似一致"。
"""
from __future__ import annotations

import numpy as np
import pytest

from core import native_kernel
from core.segment_engine.composer import LayerComposer

pytestmark = pytest.mark.skipif(
    not native_kernel.available(),
    reason="split_kernel.dll 未构建（cd native/split_kernel && cargo build --release）",
)


def _reference(stack, h, w):
    return LayerComposer(device="cpu")._alpha_over(stack, h, w)


def test_alpha_over_matches_python_byte_for_byte_on_random_layers():
    rng = np.random.default_rng(20261119)
    h, w = 97, 61  # 非对齐尺寸，专打 SIMD/行距假设
    for trial in range(8):
        n = int(rng.integers(1, 8))
        stack = [rng.integers(0, 256, (h, w, 4), dtype=np.uint8) for _ in range(n)]
        stack[0][..., 3] = 255
        stack[-1][..., 3] = 0
        # 混入 None 项（Python 参考实现会跳过）
        stack.insert(int(rng.integers(0, n + 1)), None)
        want = _reference(stack, h, w)
        got = native_kernel.alpha_over(stack, h, w)
        assert got is not None
        assert np.array_equal(got, want), f"trial {trial} 不一致"


def test_alpha_over_matches_on_real_layer_pngs(tmp_path):
    """用真实导出图层对拍（半透明腮红/眼泪是 float 截断的敏感区）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    layers_dir = root / "output" / "冰蓝星辉偶像少女_Live2D" / "rigged_1794724424" / "layers"
    pngs = sorted(layers_dir.glob("*.png")) if layers_dir.is_dir() else []
    if len(pngs) < 3:
        pytest.skip("没有真实图层产物")
    from PIL import Image

    arrays = [np.array(Image.open(p).convert("RGBA")) for p in pngs[:12]]
    h, w = arrays[0].shape[:2]
    want = _reference(arrays, h, w)
    got = native_kernel.alpha_over(arrays, h, w)
    assert got is not None
    assert np.array_equal(got, want)


def test_alpha_over_rejects_bad_shapes_and_returns_none():
    assert native_kernel.alpha_over([], 4, 4) is None
    assert native_kernel.alpha_over([np.zeros((4, 4, 3), np.uint8)], 4, 4) is None
    assert native_kernel.alpha_over([np.zeros((5, 4, 4), np.float32)], 4, 4) is None


def test_disabled_env_falls_back(monkeypatch):
    """LIVE2D_NATIVE_KERNEL=0 必须整体回退，且不影响 Python 参考实现。"""
    monkeypatch.setattr(native_kernel, "_lib", None)
    monkeypatch.setattr(native_kernel, "_load_attempted", False)
    monkeypatch.setenv("LIVE2D_NATIVE_KERNEL", "0")
    assert native_kernel.available() is False
    stack = [np.full((4, 4, 4), 128, np.uint8)]
    assert native_kernel.alpha_over(stack, 4, 4) is None
    assert _reference(stack, 4, 4) is not None
