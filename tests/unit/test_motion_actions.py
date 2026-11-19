# -*- coding: utf-8 -*-
"""出厂动作组（idle / nod / look / talk）的生成契约。

三条硬约束：
1. 曲线值不得越出参数键形区间（越界只能外推，画面会乱）；
2. 循环动作首尾同值，否则环回处跳变；
3. 参数缺失的组直接跳过，不产出「文件齐全但画面不动」的假动作。
"""
from __future__ import annotations

from live2d_builder.motion.motion3 import Motion3Builder

DRIVABLE = {
    "ParamAngleX": [-30.0, 0.0, 30.0],
    "ParamAngleY": [-30.0, 0.0, 30.0],
    "ParamAngleZ": [-30.0, 0.0, 30.0],
    "ParamBodyAngleX": [-10.0, 0.0, 10.0],
    "ParamBodyAngleY": [-10.0, 0.0, 10.0],
    "ParamEyeLOpen": [0.0, 1.0],
    "ParamEyeROpen": [0.0, 1.0],
    "ParamEyeBallX": [-1.0, 0.0, 1.0],
    "ParamEyeBallY": [-1.0, 0.0, 1.0],
    "ParamMouthOpenY": [0.0, 1.0],
    "ParamMouthForm": [-1.0, 1.0],
    "ParamCheek": [0.0, 1.0],
    "ParamBrowLY": [-1.0, 1.0],
    "ParamBrowRY": [-1.0, 1.0],
}
DEFAULTS = {
    "ParamAngleX": 0.0, "ParamAngleY": 0.0, "ParamAngleZ": 0.0,
    "ParamBodyAngleX": 0.0, "ParamBodyAngleY": 0.0,
    "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
    "ParamEyeBallX": 0.0, "ParamEyeBallY": 0.0,
    "ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0, "ParamCheek": 0.0,
    "ParamBrowLY": 0.0, "ParamBrowRY": 0.0,
}


def _values(curve) -> list[float]:
    """Segments = [t0, v0, 0, t1, v1, 0, ...]；值在索引 1, 4, 7, ..."""
    seg = curve["Segments"]
    return [seg[i] for i in range(1, len(seg), 3)]


def test_action_set_contains_expected_groups():
    docs = Motion3Builder().build_action_set(DRIVABLE, defaults=DEFAULTS)
    assert set(docs) == {"idle", "nod", "look", "talk"}
    assert docs["idle"]["Meta"]["Loop"] is True
    assert docs["nod"]["Meta"]["Loop"] is False
    assert docs["look"]["Meta"]["Loop"] is False
    assert docs["talk"]["Meta"]["Loop"] is True


def test_curve_values_stay_inside_keyform_range():
    docs = Motion3Builder().build_action_set(DRIVABLE, defaults=DEFAULTS)
    for group, doc in docs.items():
        for curve in doc["Curves"]:
            low, high = min(DRIVABLE[curve["Id"]]), max(DRIVABLE[curve["Id"]])
            for value in _values(curve):
                assert low - 1e-6 <= value <= high + 1e-6, (
                    f"{group}/{curve['Id']} 越出键形区间: {value} not in [{low},{high}]")


def test_looping_action_has_matching_endpoints():
    docs = Motion3Builder().build_action_set(DRIVABLE, defaults=DEFAULTS)
    for group in ("idle", "talk"):
        for curve in docs[group]["Curves"]:
            seg = curve["Segments"]
            first, last = seg[1], seg[-1]
            assert abs(first - last) < 1e-4, f"{group}/{curve['Id']} 首尾不一致: {first} vs {last}"


def test_non_looping_action_returns_to_rest():
    """nod / look 收尾必须回到静止值，切回 idle 时才不会拖一个偏置。"""
    docs = Motion3Builder().build_action_set(DRIVABLE, defaults=DEFAULTS)
    for group in ("nod", "look"):
        for curve in docs[group]["Curves"]:
            pid = curve["Id"]
            rest = DEFAULTS[pid]
            last = curve["Segments"][-1]
            assert abs(last - rest) < 1e-4, f"{group}/{pid} 收尾 {last} 未回到静止值 {rest}"


def test_action_moves_its_primary_parameter():
    docs = Motion3Builder().build_action_set(DRIVABLE, defaults=DEFAULTS)
    # 点头必须真的让 ParamAngleY 走起来，否则「动作存在但画面不动」
    y = next(c for c in docs["nod"]["Curves"] if c["Id"] == "ParamAngleY")
    vals = _values(y)
    assert max(vals) - min(vals) > 5.0
    # 说话必须真的让嘴开合
    mouth = next(c for c in docs["talk"]["Curves"] if c["Id"] == "ParamMouthOpenY")
    mvals = _values(mouth)
    assert max(mvals) - min(mvals) > 0.3


def test_missing_parameters_skip_the_group():
    """只有嘴部参数时：talk 仍可生成，nod / look 不产出。"""
    docs = Motion3Builder().build_action_set({"ParamMouthOpenY": [0.0, 1.0]})
    assert "talk" in docs
    assert "nod" not in docs
    assert "look" not in docs


def test_empty_drivable_yields_no_documents():
    assert Motion3Builder().build_action_set({}) == {}


def test_meta_counts_match_segments():
    docs = Motion3Builder().build_action_set(DRIVABLE, defaults=DEFAULTS)
    for group, doc in docs.items():
        meta = doc["Meta"]
        seg_count = sum((len(c["Segments"]) - 2) // 3 for c in doc["Curves"])
        point_count = sum((len(c["Segments"]) - 2) // 3 + 1 for c in doc["Curves"])
        assert meta["CurveCount"] == len(doc["Curves"])
        assert meta["TotalSegmentCount"] == seg_count
        assert meta["TotalPointCount"] == point_count
        assert meta["Duration"] > 0 and meta["Fps"] > 0
