"""FF-2「18 层顺序契约」的测量钩子。

背景：架构文档 docs/architecture/index.md 的 FF-2 承诺「STANDARD_LAYER_ORDER 与
PSD 写出顺序 + Live2D renderOrder 三者严格 1:1 匹配，任何层错位 0 容忍」，
测量来源写作 core/psd/validator.py::check_layer_order。该函数此前只存在于文档里
（全仓 0 处代码命中），于是层序错位既不会被发现也不会被阻断。

这里锁住它真正履行承诺所需的四条性质：
1. 一致时零误报（否则会被当成噪音关掉）；
2. 错位必须**点名具体层**（不能只说「不一致」）；
3. 三份来源下标基数不同时不得误报（比相对顺序，不比绝对下标）；
4. 未登记层名必须显式报出（历史事故就是它被静默兜底排到最前）。
"""

from __future__ import annotations

from core.psd.validator import check_layer_order

# 一段真实形态的权威表片段（65 项表的节选，后→前）。
_STANDARD = [
    "hair_back",
    "face_base",
    "face",
    "cheek",
    "eyelid_left",
    "eyelid_right",
    "eyewhite_left",
    "irides_left",
    "hair_front",
    "eyebrow_L",
    "nose",
    "mouth_cavity",
    "mouth",
    "neck",
    "clothes_top",
    "arm_L",
    "hand_L",
]


def _layers_in_standard():
    """实际产出通常只含权威表的一部分。"""
    return ["hair_back", "face", "cheek", "eyelid_left", "eyewhite_left",
            "irides_left", "hair_front", "eyebrow_L", "nose", "mouth_cavity",
            "mouth", "neck", "clothes_top", "arm_L", "hand_L"]


class TestNoFalsePositives:
    def test_identical_orders_report_nothing(self):
        order = _layers_in_standard()
        assert check_layer_order(_STANDARD, psd_order=order, render_order=order) == []

    def test_subset_with_same_relative_order_is_not_a_mismatch(self):
        """三份来源下标基数不同（65 项 vs 15 项），比相对顺序才不会满屏假阳性。"""
        order = _layers_in_standard()
        # psd 少了两层，但相对顺序未变 —— 不算错位。
        psd = [n for n in order if n not in ("cheek", "irides_left")]
        assert check_layer_order(_STANDARD, psd_order=psd, render_order=order) == []

    def test_only_render_available_still_checks_it(self):
        order = _layers_in_standard()
        assert check_layer_order(_STANDARD, psd_order=None, render_order=order) == []

    def test_no_sources_means_nothing_to_compare(self):
        assert check_layer_order(_STANDARD) == []


class TestMisorderIsNamed:
    def test_swapped_adjacent_layers_are_both_named(self):
        """口腔盖住嘴唇 —— 历史上真实发生过的错位。"""
        order = _layers_in_standard()
        swapped = list(order)
        i, j = swapped.index("mouth_cavity"), swapped.index("mouth")
        swapped[i], swapped[j] = swapped[j], swapped[i]

        issues = check_layer_order(_STANDARD, psd_order=order, render_order=swapped)

        named = {issue["layer"] for issue in issues}
        assert {"mouth_cavity", "mouth"} <= named, f"必须点名这两层，实际 {named}"
        for issue in issues:
            assert issue["code"] == "layer_order_mismatch"
            assert set(issue["sources"]) == {"render"}, (
                f"错位来源标注不对: {issue['sources']}")
            assert issue["message"], "错位必须带可读说明"
        # 方向必须是「画得更靠前」而不是笼统一句不一致。
        cavity = next(i for i in issues if i["layer"] == "mouth_cavity")
        assert cavity["direction"] == "moved_forward"

    def test_psd_misorder_is_reported_separately_from_render(self):
        order = _layers_in_standard()
        psd = list(order)
        psd.remove("hair_front")
        psd.insert(0, "hair_front")     # 刘海被排到最底

        issues = check_layer_order(_STANDARD, psd_order=psd, render_order=order)

        assert issues, "PSD 层序被改坏却没报出来"
        assert all(i["code"] == "layer_order_mismatch" for i in issues)
        assert "hair_front" in {i["layer"] for i in issues}
        assert "psd" in issues[0]["sources"]

    def test_reversed_order_is_reported(self):
        order = _layers_in_standard()
        issues = check_layer_order(
            _STANDARD, psd_order=order, render_order=list(reversed(order)))
        assert len(issues) >= len(order) - 2, "整体反转必须大面积报错"


class TestUnknownLayers:
    def test_layer_absent_from_authority_table_is_flagged(self):
        """未登记的层名无法判定位置 —— 必须显式报出，不许静默兜底。"""
        order = _layers_in_standard() + ["mystery_part"]
        issues = check_layer_order(_STANDARD, psd_order=order, render_order=order)

        unknown = [i for i in issues if i["code"] == "layer_unknown_to_standard"]
        assert [i["layer"] for i in unknown] == ["mystery_part"]
        assert "STANDARD_LAYER_ORDER" in unknown[0]["message"]

    def test_unknown_layer_is_not_silently_ordered(self):
        """未登记层既报 unknown，也不参与顺序比较（它没有权威位置可比）。"""
        order = _layers_in_standard()
        with_unknown = order + ["mystery_part"]
        issues = check_layer_order(
            _STANDARD, psd_order=order, render_order=with_unknown)
        assert all(i["layer"] != "mystery_part" or
                   i["code"] == "layer_unknown_to_standard" for i in issues)


class TestDeterminism:
    def test_issue_list_is_stable_for_identical_input(self):
        order = _layers_in_standard()
        swapped = list(reversed(order))
        first = check_layer_order(_STANDARD, psd_order=order, render_order=swapped)
        second = check_layer_order(_STANDARD, psd_order=order, render_order=swapped)
        assert first == second, "同一输入必须产出逐字相同的清单，否则无法断言"
