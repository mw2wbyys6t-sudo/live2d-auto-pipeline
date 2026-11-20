"""未解释区域诊断：把「哪一块前景没被任何部件认领」作为证据报出来。

为什么需要：全局覆盖率会把一整块区域平均掉。实测一张立绘有 7.7% 前景无人
认领（最大一块 2254px），全局 explained_fraction 仍有 0.92 而通过；手工定位
「丢了哪一块」只能靠翻图。这里锁住证据字段的形状与噪点过滤口径。
"""
from __future__ import annotations

import numpy as np

from core.segment_engine import layer_quality as LQ

H = W = 200


def _rect(x0, y0, x1, y1):
    m = np.zeros((H, W), dtype=bool)
    m[y0:y1, x0:x1] = True
    return m


def test_lost_block_is_reported_with_bbox() -> None:
    fg = _rect(10, 10, 190, 190)
    masks = {"hair": _rect(10, 10, 190, 100), "clothes": _rect(10, 100, 190, 160)}
    # 未被认领：y 160-190 的整条带（30px 高 × 180 宽 = 5400px）

    regions = LQ.unexplained_regions(masks, fg)

    assert len(regions) == 1
    region = regions[0]
    assert region["area"] == 5400
    assert region["bbox"] == [10, 160, 189, 189]
    assert 0.0 < float(region["ratio"]) < 1.0


def test_tiny_specks_are_ignored() -> None:
    """发丝与抗锯齿边会产生零碎空洞；小于阈值的碎块不进报告。"""
    fg = _rect(10, 10, 190, 190)
    masks = {"all": fg.copy()}
    for x in range(20, 180, 6):
        masks["all"][100:103, x:x + 3] = False  # 每块 9px

    assert LQ.unexplained_regions(masks, fg) == []


def test_regions_are_sorted_by_area_and_capped() -> None:
    big = 400
    fg = np.ones((big, big), dtype=bool)
    masks = {"all": fg.copy()}
    # 造 6 块互不相接的空洞，面积递减（按 60px 行距隔开）；只有前 5 块该出现
    for i in range(6):
        size = 40 - i * 2
        y = 10 + i * 60
        masks["all"][y:y + size, 10:10 + size] = False

    regions = LQ.unexplained_regions(masks, fg)

    assert len(regions) == LQ.UNEXPLAINED_REGION_TOP_N == 5
    areas = [int(r["area"]) for r in regions]
    assert areas == sorted(areas, reverse=True)


def test_score_masks_reports_regions_without_changing_the_gate() -> None:
    """证据字段必须出现，但缺陷计数与闸门口径不能被它改变。"""
    fg = _rect(10, 10, 190, 190)
    masks = {
        "hair_back": _rect(10, 10, 190, 90),
        "hair_front": _rect(40, 10, 160, 60),
        "face": _rect(60, 60, 140, 120),
        "eyes_left": _rect(62, 70, 92, 86),
        "eyes_right": _rect(108, 70, 138, 86),
        "mouth": _rect(88, 100, 112, 112),
        "neck": _rect(80, 120, 120, 140),
        "clothes_top": _rect(30, 140, 170, 170),
    }
    baseline = LQ.score_masks(masks, LQ.REQUIRED_PARTS, fg)
    assert baseline["unexplained_regions"] is not None
    assert any(int(r["area"]) > 0 for r in baseline["unexplained_regions"])

    # 同样的输入，去掉该字段后缺陷计数应完全一致（说明只是旁证）
    stripped = dict(baseline)
    stripped.pop("unexplained_regions")
    assert stripped["defect_count"] == baseline["defect_count"]
    assert LQ.verdict(stripped)["ok"] == LQ.verdict(baseline)["ok"]


def test_no_foreground_means_no_report() -> None:
    masks = {"face": _rect(10, 10, 50, 50)}
    data = LQ.score_masks(masks, LQ.REQUIRED_PARTS, None)
    assert data["unexplained_regions"] is None
