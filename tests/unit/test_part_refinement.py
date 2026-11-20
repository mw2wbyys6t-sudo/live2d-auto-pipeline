"""部件细化（刘海分束 / 后侧发再分束 / 手臂分段 / 裙内层）的单元测试。

覆盖两类情形：
1. 几何上真的分得开 -> 拆出子层，且子层互不重叠、并集等于原层；
2. 面积/形状不满足阈值 -> **原样保留原层**（绝不产出空层，也不吃掉原层）。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from core.segment_engine.composer import LayerComposer
from core.segment_engine.part_split import (
    ARM_SEGMENT_MIN_HEIGHT_PX,
    HAIR_BANGS_SIDE_MIN_RATIO,
    split_hair_bangs_strands,
    split_hair_side_strands,
    split_leg_segments,
    split_limb_segments,
    split_skirt_inner,
)


def _rect(h: int, w: int, x0: int, x1: int, y0: int, y1: int) -> np.ndarray:
    mask = np.zeros((h, w), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


# ---------------------------------------------------------------------------
# 刘海分束
# ---------------------------------------------------------------------------


def test_bangs_split_into_left_and_right_when_both_tufts_are_big_enough() -> None:
    """两块独立发绺分居中线两侧 -> hair_front_left / hair_front_right。"""
    h, w = 120, 200
    face = _rect(h, w, 95, 105, 25, 45)          # 中线 = 99.5
    bangs = (_rect(h, w, 40, 90, 10, 40)          # 观察者左 = 角色右
             | _rect(h, w, 110, 160, 10, 40))     # 观察者右 = 角色左

    masks, warnings = split_hair_bangs_strands({"face": face, "hair_front": bangs})

    assert "hair_front" not in masks
    assert masks["hair_front_left"][10:40, 110:160].all()
    assert masks["hair_front_right"][10:40, 40:90].all()
    assert not (masks["hair_front_left"] & masks["hair_front_right"]).any()
    assert int((masks["hair_front_left"] | masks["hair_front_right"]).sum()) == int(bangs.sum())
    assert any("左右两束" in message for message in warnings)


def test_single_block_bangs_stay_whole() -> None:
    """只有一块连通域不拆：硬切平直刘海会留下一条没有画稿的直缝。"""
    h, w = 120, 200
    bangs = _rect(h, w, 30, 170, 10, 40)

    masks, warnings = split_hair_bangs_strands({"face": _rect(h, w, 95, 105, 25, 45),
                                                "hair_front": bangs})

    assert set(masks) == {"face", "hair_front"}
    assert int(masks["hair_front"].sum()) == int(bangs.sum())
    assert not warnings


def test_tiny_side_tuft_keeps_bangs_whole() -> None:
    """某侧面积低于阈值（15%）时不拆，碎点不算独立发绺。"""
    h, w = 120, 200
    bangs = (_rect(h, w, 40, 90, 10, 40)          # 1500 px
             | _rect(h, w, 110, 118, 10, 40))     # 240 px = 13.8% < 15%

    masks, warnings = split_hair_bangs_strands({"face": _rect(h, w, 95, 105, 25, 45),
                                                "hair_front": bangs})

    assert "hair_front" in masks
    assert "hair_front_left" not in masks
    assert int(masks["hair_front"].sum()) == int(bangs.sum())
    assert not warnings
    assert 240 / int(bangs.sum()) < HAIR_BANGS_SIDE_MIN_RATIO


def test_hair_bangs_alias_is_split_too() -> None:
    """``hair_bangs`` 与 ``hair_front`` 是同一位置的两种命名，都要能拆。"""
    h, w = 120, 200
    bangs = (_rect(h, w, 40, 90, 10, 40) | _rect(h, w, 110, 160, 10, 40))

    masks, _ = split_hair_bangs_strands({"hair_bangs": bangs})

    assert {"hair_bangs_left", "hair_bangs_right"} <= set(masks)
    assert "hair_bangs" not in masks


# ---------------------------------------------------------------------------
# 后侧发再分束
# ---------------------------------------------------------------------------


def test_side_strand_splits_into_parent_plus_extra_strand() -> None:
    """侧束内部两块连通域分得开 -> 较大的一束保留原名，另一束叫 *_outer。"""
    h, w = 120, 200
    face = _rect(h, w, 95, 105, 25, 45)                       # 中线 = 99.5
    strand = (_rect(h, w, 120, 140, 10, 110)                  # 2000 px
              | _rect(h, w, 150, 165, 10, 110))               # 1500 px

    masks, warnings = split_hair_side_strands({"face": face, "hair_back_left": strand})

    assert "hair_back_left" in masks
    assert "hair_back_left_outer" in masks
    assert int(masks["hair_back_left"].sum()) == 2000
    assert int(masks["hair_back_left_outer"].sum()) == 1500
    assert not (masks["hair_back_left"] & masks["hair_back_left_outer"]).any()
    assert (int(masks["hair_back_left"].sum())
            + int(masks["hair_back_left_outer"].sum())) == int(strand.sum())
    assert any("再分出一束" in message for message in warnings)


def test_flat_side_strand_stays_whole() -> None:
    """一整条平发没有谷 -> 不拆（假缝比少一条摆链更糟）。"""
    h, w = 120, 200
    strand = _rect(h, w, 120, 160, 10, 110)

    masks, warnings = split_hair_side_strands(
        {"face": _rect(h, w, 95, 105, 25, 45), "hair_back_left": strand})

    assert set(masks) == {"face", "hair_back_left"}
    assert int(masks["hair_back_left"].sum()) == int(strand.sum())
    assert not warnings


# ---------------------------------------------------------------------------
# 手臂分段
# ---------------------------------------------------------------------------


def test_arm_is_split_at_the_elbow() -> None:
    h, w = 200, 200
    arm = _rect(h, w, 60, 80, 20, 140)

    masks, warnings = split_limb_segments({"arm_L": arm})

    assert "arm_L" not in masks
    assert "arm_upper_L" in masks and "arm_lower_L" in masks
    upper_rows = np.where(masks["arm_upper_L"].any(axis=1))[0]
    lower_rows = np.where(masks["arm_lower_L"].any(axis=1))[0]
    assert upper_rows.max() < lower_rows.min()
    assert int((masks["arm_upper_L"] | masks["arm_lower_L"]).sum()) == int(arm.sum())
    assert not (masks["arm_upper_L"] & masks["arm_lower_L"]).any()
    assert any("肘部" in message for message in warnings)


def test_short_arm_is_not_split() -> None:
    """低于最小高度（40px）的手臂没有两段可动空间，原样保留。"""
    h, w = 200, 200
    arm = _rect(h, w, 60, 80, 20, 20 + ARM_SEGMENT_MIN_HEIGHT_PX - 1)

    masks, warnings = split_limb_segments({"arm_R": arm})

    assert set(masks) == {"arm_R"}
    assert int(masks["arm_R"].sum()) == int(arm.sum())
    assert not warnings


def test_leg_is_split_at_the_knee() -> None:
    h, w = 240, 200
    leg = _rect(h, w, 60, 80, 20, 220)

    masks, warnings = split_leg_segments({"leg_L": leg})

    assert "leg_L" not in masks
    assert "thigh_L" in masks and "calf_L" in masks
    thigh_rows = np.where(masks["thigh_L"].any(axis=1))[0]
    calf_rows = np.where(masks["calf_L"].any(axis=1))[0]
    assert thigh_rows.max() < calf_rows.min()
    assert int((masks["thigh_L"] | masks["calf_L"]).sum()) == int(leg.sum())
    assert not (masks["thigh_L"] & masks["calf_L"]).any()
    assert any("膝盖" in message for message in warnings)


def test_short_leg_is_not_split() -> None:
    """腿太矮时保留整段：分出来只是两条没有可动空间的碎条。"""
    h, w = 200, 200
    leg = _rect(h, w, 60, 80, 20, 20 + 39)

    masks, warnings = split_leg_segments({"leg_R": leg})

    assert set(masks) == {"leg_R"}
    assert int(masks["leg_R"].sum()) == int(leg.sum())
    assert not warnings


def test_speckled_side_keeps_bangs_whole() -> None:
    """某一侧自己是一堆碎点（最大连通块 <60%）时不拆：否则质量门会报碎片化。"""
    h, w = 120, 200
    bangs = _rect(h, w, 40, 90, 10, 40)                       # 右侧实心 1500 px
    for x in range(105, 165, 12):                             # 左侧 5 条 8px 竖条
        bangs |= _rect(h, w, x, x + 8, 10, 40)

    masks, warnings = split_hair_bangs_strands(
        {"face": _rect(h, w, 95, 105, 25, 45), "hair_front": bangs})

    assert set(masks) == {"face", "hair_front"}
    assert int(masks["hair_front"].sum()) == int(bangs.sum())
    assert not warnings


def test_arm_with_speckled_masks_is_not_split() -> None:
    """手臂掩码里混着一堆独立碎块（袖口反光）时不拆，两段都会带着碎片。"""
    h, w = 200, 200
    arm = _rect(h, w, 60, 80, 20, 140)                        # 主体 2400 px
    for x in range(100, 190, 12):
        arm |= _rect(h, w, x, x + 8, 24, 44)                  # 上半区 8 块碎片

    masks, warnings = split_limb_segments({"arm_L": arm})

    assert set(masks) == {"arm_L"}
    assert int(masks["arm_L"].sum()) == int(arm.sum())
    assert not warnings


# ---------------------------------------------------------------------------
# 裙内层
# ---------------------------------------------------------------------------


def test_skirt_inner_is_the_eroded_core() -> None:
    h, w = 160, 200
    skirt = _rect(h, w, 20, 180, 20, 120)

    masks, warnings = split_skirt_inner({"clothes_bottom": skirt})

    assert "clothes_bottom" in masks and "skirt_inner" in masks
    outer, inner = masks["clothes_bottom"], masks["skirt_inner"]
    assert not (outer & inner).any()
    assert int((outer | inner).sum()) == int(skirt.sum())
    # 内层必须真的在里面，外轮廓因此是有洞的环
    assert int((inner & ~skirt).sum()) == 0
    assert not outer[70, 100]
    assert any("skirt_inner" in message for message in warnings)


def test_thin_skirt_gets_no_inner_layer() -> None:
    """腐蚀把整块吃光的细裙不拆，否则只能交出一层空图。"""
    h, w = 160, 200
    skirt = _rect(h, w, 20, 180, 20, 24)

    masks, warnings = split_skirt_inner({"clothes_bottom": skirt})

    assert set(masks) == {"clothes_bottom"}
    assert int(masks["clothes_bottom"].sum()) == int(skirt.sum())
    assert not warnings


# ---------------------------------------------------------------------------
# 绘制顺序与装配
# ---------------------------------------------------------------------------


def test_new_layer_names_are_registered_in_draw_order() -> None:
    """新层名必须显式登记，否则 _draw_order 会按字母序把它们画到最上层。"""
    order = LayerComposer.STANDARD_LAYER_ORDER
    new_names = (
        "hair_front_left", "hair_front_right",
        "hair_bangs_left", "hair_bangs_right",
        "hair_back_left_inner", "hair_back_left_outer",
        "hair_back_right_inner", "hair_back_right_outer",
        "arm_upper_L", "arm_lower_L", "arm_upper_R", "arm_lower_R",
        "thigh_L", "calf_L", "thigh_R", "calf_R",
        "skirt_inner",
    )
    for name in new_names:
        assert name in order, f"{name} 没进 STANDARD_LAYER_ORDER"

    idx = {name: index for index, name in enumerate(order)}
    # 后发侧束的第三束画在父束之后（父束是 amodal 层，补画会盖住它前面的同族层）
    assert idx["hair_back_left_inner"] < idx["hair_back_left"] < idx["face_base"]
    # 刘海仍在闭眼图之后、眉毛之前
    assert idx["hair_front_left"] > idx["eyelid_right"]
    assert idx["hair_bangs"] < idx["eyebrow_R"]
    # 上臂在小臂之后，二者都在手之前
    assert idx["arm_upper_L"] < idx["arm_lower_L"] < idx["hands"]
    # 大腿在小腿之后，二者都在袜子之前
    assert idx["thigh_L"] < idx["calf_L"] < idx["legwear"]
    # 裙内层在裙子外轮廓之后、裙摆之前
    assert idx["skirt_inner"] < idx["skirt"] < idx["skirt_hem"]


def test_compose_applies_the_refinement(tmp_path) -> None:
    """细化必须真的接进 compose：拿到的产物里要出现分段后的手臂层。"""
    h, w = 200, 200
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arm = _rect(h, w, 60, 80, 20, 140)
    arr[arm] = (40, 90, 160, 255)

    layers = LayerComposer().compose(
        {"arm_L": arm}, Image.fromarray(arr, "RGBA"), str(tmp_path))

    assert {"arm_upper_L", "arm_lower_L"} <= set(layers)
    assert "arm_L" not in layers
    for name in ("arm_upper_L", "arm_lower_L"):
        assert layers[name]["pixel_count"] > 0


def test_compose_keeps_skirt_hem_separate_from_skirt_inner(tmp_path) -> None:
    h, w = 220, 180
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    skirt = _rect(h, w, 20, 160, 35, 180)
    skirt[181:195, 70:110] = True
    arr[skirt] = (120, 80, 200, 255)

    layers = LayerComposer(device="cpu").compose(
        {"clothes_bottom": skirt}, Image.fromarray(arr, "RGBA"), str(tmp_path))

    alpha = {
        name: np.asarray(Image.open(info["path"]).convert("RGBA"))[..., 3] > 0
        for name, info in layers.items()
        if name in {
            "clothes_bottom", "skirt_inner", "skirt_hem_L", "skirt_hem_R", "waistband"
        }
    }
    assert {"clothes_bottom", "skirt_inner", "skirt_hem_L", "skirt_hem_R", "waistband"} <= set(alpha)
    hems = alpha["skirt_hem_L"] | alpha["skirt_hem_R"]
    assert not (hems & alpha["skirt_inner"]).any()
    assert not (hems & alpha["clothes_bottom"]).any()
    reconstructed = np.logical_or.reduce(list(alpha.values()))
    assert np.array_equal(reconstructed, skirt)
