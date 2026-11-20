"""P1 眼/口细节拆分（split_eye_details / split_mouth_details）的证据闸门测试。

对应 staging spec 2026-11-19 的 in_scope_B：只在像素证据充分时拆分，
证据不足保留母层并给出机器码，绝不臆造部件。
"""
from __future__ import annotations

import numpy as np

from core.segment_engine.part_split import (
    SplitNote,
    split_eye_details,
    split_mouth_details,
)


def _codes(warnings) -> set:
    return {getattr(w, "code", None) for w in warnings}


# ---------------------------------------------------------------------------
# 眼部：瞳孔 / 高光 / 上下睫毛
# ---------------------------------------------------------------------------

def test_iris_splits_into_pupil_with_conservation() -> None:
    h, w = 60, 60
    iris = np.zeros((h, w), dtype=bool)
    iris[20:40, 20:40] = True
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[20:40, 20:40] = (90, 90, 120)      # 虹膜底色
    img[27:33, 27:33] = (20, 20, 30)       # 中央瞳芯（暗）

    masks, _warnings = split_eye_details({"irides_L": iris}, img)

    assert "eye_pupil_L" in masks
    assert "irides_L" in masks
    pupil, rest = masks["eye_pupil_L"], masks["irides_L"]
    assert not (pupil & rest).any(), "瞳孔与虹膜余量不得重叠"
    assert int((pupil | rest).sum()) == int(iris.sum()), "拆分必须守恒"


def test_uniform_iris_is_kept_whole_with_evidence_code() -> None:
    h, w = 60, 60
    iris = np.zeros((h, w), dtype=bool)
    iris[20:40, 20:40] = True
    img = np.full((h, w, 3), 100, dtype=np.uint8)  # 毫无明暗差

    masks, warnings = split_eye_details({"irides_L": iris}, img)

    assert "irides_L" in masks and "eye_pupil_L" not in masks
    assert "eye_detail_low_evidence" in _codes(warnings)


def test_specular_highlight_is_split_out() -> None:
    h, w = 60, 60
    iris = np.zeros((h, w), dtype=bool)
    iris[20:40, 20:40] = True
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[20:40, 20:40] = (80, 80, 110)
    img[22:25, 22:25] = (255, 255, 255)    # 镜面高光
    img[30:36, 30:36] = (15, 15, 25)       # 瞳孔

    masks, _warnings = split_eye_details({"irides_L": iris}, img)

    assert "eye_highlight_L" in masks
    assert masks["eye_highlight_L"][23, 23]
    assert int(masks["eye_highlight_L"].sum()) == 9


def test_eyelash_splits_into_upper_and_lower() -> None:
    h, w = 60, 60
    lash = np.zeros((h, w), dtype=bool)
    lash[10:20, 20:40] = True   # 上睫毛
    lash[40:50, 20:40] = True   # 下睫毛
    img = np.zeros((h, w, 3), dtype=np.uint8)

    masks, _warnings = split_eye_details({"eyelash_L": lash}, img)

    assert "eyelash_L" not in masks
    assert masks["eyelash_upper_L"][15, 25]
    assert masks["eyelash_lower_L"][45, 25]
    assert int(masks["eyelash_upper_L"].sum() + masks["eyelash_lower_L"].sum()) == int(lash.sum())


def test_thin_eyelash_is_not_forced_apart() -> None:
    h, w = 60, 60
    lash = np.zeros((h, w), dtype=bool)
    lash[30:32, 20:40] = True   # 只有一条细睫毛，上下比例失衡

    masks, warnings = split_eye_details({"eyelash_L": lash}, np.zeros((h, w, 3), np.uint8))

    assert "eyelash_L" in masks
    assert "lash_not_separable" in _codes(warnings)


# ---------------------------------------------------------------------------
# 口部：牙齿 / 舌头 / 上下唇
# ---------------------------------------------------------------------------

def test_open_mouth_splits_teeth_and_lips() -> None:
    h, w = 60, 60
    mouth = np.zeros((h, w), dtype=bool)
    mouth[20:40, 20:40] = True
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[20:40, 20:40] = (120, 40, 45)      # 唇色
    img[22:28, 22:38] = (250, 250, 250)    # 上排牙齿

    masks, _warnings = split_mouth_details({"mouth": mouth}, img)

    assert "mouth" not in masks
    assert "teeth" in masks
    assert "lip_upper" in masks or "lip_lower" in masks
    union = np.zeros((h, w), dtype=bool)
    for key in ("teeth", "lip_upper", "lip_lower", "tongue"):
        if key in masks:
            union |= masks[key]
    assert not (masks["teeth"] & masks.get("lip_lower", np.zeros_like(mouth))).any()
    assert int(union.sum()) == int(mouth.sum()), "口部细节拆分必须守恒"


def test_closed_mouth_line_is_kept_whole() -> None:
    h, w = 60, 60
    mouth = np.zeros((h, w), dtype=bool)
    mouth[30:32, 20:40] = True             # 2px 闭口线
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[30:32, 20:40] = (30, 20, 20)

    masks, warnings = split_mouth_details({"mouth": mouth}, img)

    assert "mouth" in masks
    assert "mouth_closed_line" in _codes(warnings)


def test_open_mouth_without_colour_evidence_keeps_whole() -> None:
    h, w = 60, 60
    mouth = np.zeros((h, w), dtype=bool)
    mouth[20:40, 20:40] = True
    img = np.full((h, w, 3), 90, dtype=np.uint8)   # 均匀灰，无牙/舌

    masks, warnings = split_mouth_details({"mouth": mouth}, img)

    assert "mouth" in masks
    assert "mouth_detail_low_evidence" in _codes(warnings)


def test_missing_mouth_is_a_noop() -> None:
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    masks, warnings = split_mouth_details({}, img)
    assert masks == {}
    assert warnings == []