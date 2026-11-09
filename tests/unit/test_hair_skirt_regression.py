"""发丝与裙摆分层的回归测试。

问题背景（本机实测，1024x1536 立绘）：
``_fallback_color_segment`` 会把 ``hair_back`` 扩到 418875 像素、bbox 覆盖
(156,13)-(903,1489) 几乎整个人物，而 ``clothes_bottom`` 只剩 3410 像素。
浅色长发与裙子同色系时，裙摆被并进后发层，导出到 Live2D 后裙子会跟着头动。

真实图片不入库：通过环境变量 ``LIVE2D_IDOL_FIXTURE`` 指向本机原图；
未设置或文件不存在时跳过，保证 CI 与离线环境可运行。
"""

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.segment_engine.semantic import SemanticLayerer

FIXTURE_ENV = "LIVE2D_IDOL_FIXTURE"

#: 裙摆所在的纵向区间（占图高比例）与横向区间（占图宽比例）。
SKIRT_ROWS = (0.46, 0.79)
SKIRT_COLS = (0.20, 0.83)
#: 长发应保留的两侧竖带（占图宽比例），用来防止修复变成「把头发全删掉」。
SIDE_LEFT_COLS = (0.10, 0.24)
SIDE_RIGHT_COLS = (0.76, 0.90)
SIDE_ROWS = (0.22, 0.46)


def _fixture() -> Image.Image:
    raw = os.environ.get(FIXTURE_ENV)
    if not raw:
        pytest.skip(f"未设置 {FIXTURE_ENV}，跳过需要本机原图的回归")
    path = Path(raw)
    if not path.is_file():
        pytest.skip(f"{FIXTURE_ENV} 指向的文件不存在: {path}")
    return Image.open(path).convert("RGBA")


def _parts() -> tuple[dict, tuple[int, int]]:
    image = _fixture()
    seg = SemanticLayerer(device="cpu", model_type="isnet")
    # 走真实前景 + 颜色分件：与 ``layer()`` 完全一致，才代表用户看到的输出。
    parts = seg.segment(image)
    return parts, image.size


def _band(mask: np.ndarray, size: tuple[int, int], rows, cols) -> np.ndarray:
    width, height = size
    return mask[int(rows[0] * height) : int(rows[1] * height), int(cols[0] * width) : int(cols[1] * width)]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知缺陷：浅色长发与白裙同色系时，裙摆仅约 5604 像素进入衣服层"
        "（预期 ≥20000）。剩余原因：白裙像素被判成肤色（约 74k）进入颈/脸层，"
        "以及中轴线外的裙摆仍留在发层（约 93k）。修复后本用例应转绿并移除标记。"
    ),
)
def test_skirt_is_not_absorbed_by_hair():
    """裙摆主体必须落在衣服层，而不是被后发层吃掉。"""
    parts, size = _parts()
    hair = parts.get("hair_back", np.zeros(size[::-1], bool)) | parts.get(
        "hair_front", np.zeros(size[::-1], bool)
    )
    bottom = parts.get("clothes_bottom", np.zeros(size[::-1], bool))

    hair_in_skirt = int(_band(hair, size, SKIRT_ROWS, SKIRT_COLS).sum())
    clothes_in_skirt = int(_band(bottom, size, SKIRT_ROWS, SKIRT_COLS).sum())

    assert clothes_in_skirt >= 20000, f"裙摆未进入衣服层：仅 {clothes_in_skirt} 像素"
    assert hair_in_skirt <= 20000, f"裙摆被并入发层：{hair_in_skirt} 像素"


def test_long_side_hair_is_kept():
    """修裙摆不能把两侧垂下的长发一起删掉。"""
    parts, size = _parts()
    hair = parts.get("hair_back", np.zeros(size[::-1], bool)) | parts.get(
        "hair_front", np.zeros(size[::-1], bool)
    )

    left = int(_band(hair, size, SIDE_ROWS, SIDE_LEFT_COLS).sum())
    right = int(_band(hair, size, SIDE_ROWS, SIDE_RIGHT_COLS).sum())

    assert left > 2000, f"左侧长发丢失：{left} 像素"
    assert right > 2000, f"右侧长发丢失：{right} 像素"
