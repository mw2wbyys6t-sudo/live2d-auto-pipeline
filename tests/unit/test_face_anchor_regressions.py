"""脸锚点 / 左右眼拆分的回归。

现场（2026-11-14，1024×1536 偶像立绘）：浅米色头发满足旧的肤色阈值，
脸和头发连成一块，脸框取成整个脑袋，眼睛锚点落到 y=264（真实约 150），
右眼最终只剩一块发丝碎片。另外旧实现按「双眼中心」硬切 ROI，关键点偏一点
就会把较小的那只眼削掉一半。
"""

from types import SimpleNamespace

import numpy as np

from core.segment_engine.face_detect import skin_mask
from core.segment_engine.semantic import SemanticSegmenter


def _solid(rgb):
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[:, :] = rgb
    return img


def test_warm_skin_is_detected():
    assert skin_mask(_solid((240, 215, 200))).all()


def test_light_beige_hair_is_not_skin():
    # 实测发色 (210,202,196)：r-g=8、r-b=14，不足以说明「暖」，
    # 旧阈值会把它当皮肤，导致脸框=整个脑袋。
    assert not skin_mask(_solid((210, 202, 196))).any()


def test_neutral_white_clothes_are_not_skin():
    assert not skin_mask(_solid((245, 245, 245))).any()


def test_eyes_are_split_by_component_not_by_midline():
    """关键点偏向一侧时，两侧眼睛仍应各自完整（不被中线削掉）。"""
    h, w = 100, 220
    non_skin = np.zeros((h, w), dtype=bool)
    non_skin[30:50, 40:80] = True    # 左眼 40×20
    non_skin[30:50, 120:160] = True  # 右眼 40×20
    skin_tol = ~non_skin
    fg = np.ones((h, w), dtype=bool)
    anchors = SimpleNamespace(
        box=(20, 0, 200, 80),
        right_eye=(70, 40),   # 关键点故意偏左
        left_eye=(150, 40),
        eye_distance=80,
    )

    left, right = SemanticSegmenter._eye_parts(anchors, skin_tol, fg, (h, w))

    widths = []
    for mask in (left, right):
        xs = np.where(mask.any(axis=0))[0]
        assert xs.size > 0, "两侧眼睛都必须存在"
        widths.append(int(xs.max() - xs.min() + 1))
    assert min(widths) / max(widths) >= 0.7, f"左右眼宽度差过大: {widths}"
