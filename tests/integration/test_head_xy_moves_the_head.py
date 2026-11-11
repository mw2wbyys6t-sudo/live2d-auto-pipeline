"""官方 Core 里，头的 X/Y（点头/侧看）必须真的让画面变化。

回归背景：``moc3_pipeline`` 里算头部变换选项的门控漏了 ``"head_xy"``，
``options`` 里没有 head 项 → ``_apply_head`` 原样返回 → HeadRig 的九张控制
网格**完全相同** → ``ParamAngleX`` / ``ParamAngleY`` 在官方 Core 里**逐像素
没有任何变化**（角色只能绕 Z 转）。

单测（``tests/unit/test_head_xy_profile.py``）钉住的是"控制网格随键变化"；
这条钉住的是**产品级**结果：官方内核渲染出来的画面必须跟着参数变。

复现：tools/rig_visual_gate.py（同一份产物渲染两帧比 SHA）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import render_probe


def _has_live2d() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要官方 Cubism Core（live2d-py）")
requires_pixels = pytest.mark.skipif(
    os.environ.get("LIVE2D_TEST_PIXELS") != "1",
    reason="需要 OpenGL：设 LIVE2D_TEST_PIXELS=1 开启像素验证")


def _head_layers():
    """最小的头：脸 + 鼻 + 耳 + 眼 + 嘴 + 发 + 身体（层名要命中作者表）。"""
    import numpy as np
    from PIL import Image

    def layer(box, color=(200, 120, 100, 255), w=300, h=420):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        arr = np.array(img)
        x0, y0, x1, y1 = box
        arr[y0:y1, x0:x1] = color
        return Image.fromarray(arr, "RGBA")

    return {
        "face": layer((90, 40, 210, 200), (255, 220, 180, 255)),
        "nose": layer((140, 110, 160, 140)),
        "cheek": layer((100, 150, 130, 180)),
        "ear_L": layer((80, 90, 92, 120)),
        "ear_R": layer((208, 90, 220, 120)),
        "eyelid_left": layer((105, 85, 135, 95)),
        "eyelid_right": layer((165, 85, 195, 95)),
        "eye_L": layer((108, 88, 132, 104)),
        "eye_R": layer((168, 88, 192, 104)),
        "mouth": layer((135, 140, 165, 158)),
        "hair_back": layer((70, 20, 230, 300), (80, 80, 200, 255)),
        "hair_front": layer((80, 30, 220, 160), (60, 60, 180, 255)),
        "clothes_top": layer((70, 210, 230, 390), (50, 150, 50, 255)),
    }


@requires_live2d
@requires_pixels
def test_the_head_actually_moves_in_x_and_y(tmp_path):
    """ParamAngleX / ParamAngleY 必须改变官方 Core 渲染出来的画面。"""
    from live2d_builder.pipeline import Live2DBuilder

    builder = Live2DBuilder(output_dir=str(tmp_path), character_name="headxy")
    builder.build(_head_layers())
    manifest = next(tmp_path.glob("*.model3.json"))
    assert manifest.is_file(), "没产出 model3.json"

    rest = render_probe(str(manifest), {})
    assert rest["ok"], rest.get("blocker")

    for parameter in ("ParamAngleX", "ParamAngleY"):
        moved = render_probe(str(manifest), {parameter: 30.0})
        assert moved["ok"], (parameter, moved.get("blocker"))
        assert moved["pixels_sha256"] != rest["pixels_sha256"], (
            f"{parameter}=30 在官方 Core 里逐像素没有变化 —— "
            "头的 X/Y 形变是空壳（head_xy 的控制网格没随参数变）")
