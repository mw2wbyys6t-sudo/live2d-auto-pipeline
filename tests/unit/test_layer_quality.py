"""质量门（layer_quality）的命名归一与结构层去碎点。

为什么要单独锁这个：合成器会把 ``eyes_left`` 拆成 ``eyewhite_left`` +
``irides_left``、把 ``legs`` 拆成 ``leg_L``/``leg_R``。质量门如果拿原始层名
直接比对，就会把**已经分好的**部件报成缺失——实测在 demo 输入上白报 2 条
缺陷，正好把 defect_count 顶到 ``MAX_DEFECTS``，产品于是把自己的正确输出
判成失败。这里是那条修复的回归测试。
"""

import io
import contextlib

import numpy as np
import pytest

from core.segment_engine import layer_quality as LQ
from core.segment_engine.semantic import SemanticLayerer

H = W = 200


def rect(x0, y0, x1, y1):
    m = np.zeros((H, W), dtype=bool)
    m[y0:y1, x0:x1] = True
    return m


def clean_parts():
    return {
        "hair_back": rect(20, 10, 180, 120),
        "hair_front": rect(60, 10, 140, 55),
        "face": rect(70, 30, 130, 100),
        "eyebrows": rect(75, 40, 125, 48),
        "eyes_left": rect(78, 50, 96, 62),
        "eyes_right": rect(104, 50, 122, 62),
        "nose": rect(95, 66, 105, 78),
        "mouth": rect(85, 82, 115, 94),
        "neck": rect(88, 100, 112, 118),
        "clothes_top": rect(60, 118, 140, 160),
        "clothes_bottom": rect(65, 150, 135, 185),
        "arms": rect(40, 120, 60, 165),
        "hands": rect(38, 165, 52, 180),
        "legs": rect(80, 185, 120, 199),
        "accessories": rect(95, 120, 105, 150),
    }


class TestCanonicalNaming:
    def test_split_eye_names_satisfy_required_parts(self):
        parts = clean_parts()
        parts.pop("eyes_left")
        parts.pop("eyes_right")
        parts["eyewhite_left"] = rect(78, 50, 96, 62)
        parts["eyewhite_right"] = rect(104, 50, 122, 62)

        assert LQ.check_required(parts) == []
        assert "eyes_left" not in LQ.missing_standard(parts)
        assert "eyes_right" not in LQ.missing_standard(parts)

    def test_split_leg_name_satisfies_unsided_requirement(self):
        parts = clean_parts()
        parts.pop("legs")
        parts["leg_L"] = rect(80, 185, 100, 199)
        parts["leg_R"] = rect(100, 185, 120, 199)

        assert "legs" not in LQ.missing_standard(parts)

    def test_missing_side_is_still_reported(self):
        """只给了左眼时，右眼该继续报缺失——归一不能变成「只要有眼睛就算」。"""
        parts = clean_parts()
        parts.pop("eyes_right")

        assert any("eyes_right" in msg for msg in LQ.check_required(parts))

    def test_eye_is_not_satisfied_by_iris_alone_when_white_missing(self):
        parts = clean_parts()
        parts.pop("eyes_left")
        parts["irides_left"] = rect(84, 52, 92, 60)

        # 虹膜归一为 eye_iris，不等同于眼白；左眼仍算缺失
        assert any("eyes_left" in msg for msg in LQ.check_required(parts))

    def test_unknown_names_are_not_merged_together(self):
        index = LQ.canonical_index({
            "whatever_a": rect(0, 0, 5, 5),
            "whatever_b": rect(10, 10, 15, 15),
        })

        assert index == {}

    def test_split_layers_union_keeps_pixels(self):
        left = rect(78, 50, 96, 62)
        right = rect(104, 50, 122, 62)
        index = LQ.canonical_index({
            "eyewhite_left": left,
            "irides_left": left,
            "eyewhite_right": right,
        })

        merged = LQ.part_mask(index, "eyes_left")
        assert merged is not None
        assert int(merged.sum()) == int(left.sum())


class TestDespeckle:
    def test_stray_dots_are_dropped_and_blocks_kept(self):
        mask = np.zeros((60, 60), dtype=bool)
        mask[10:40, 10:40] = True          # 真正的一块
        mask[2, 2] = mask[2, 3] = mask[50, 55] = True   # 抗锯齿碎点

        out = SemanticLayerer._despeckle(mask, min_area=16)

        assert out[10:40, 10:40].all()
        assert not out[2, 2] and not out[2, 3] and not out[50, 55]

    def test_small_but_solid_part_survives_when_budget_allows(self):
        """鼻子只有几个像素，阈值定得比它小的时候必须原样保留。"""
        mask = np.zeros((30, 30), dtype=bool)
        mask[10:13, 10:13] = True          # 9 px

        out = SemanticLayerer._despeckle(mask, min_area=4)

        assert int(out.sum()) == 9

    def test_empty_mask_is_returned_unchanged(self):
        mask = np.zeros((10, 10), dtype=bool)

        assert not SemanticLayerer._despeckle(mask, min_area=4).any()


class TestSynthesizedOverlayIsNotAnOverExtension:
    """合成层（闭眼图 / 口内）与它**取自**的层 100% 重叠是设计，不是越界扩张。

    闭眼图就是眼睛那块面积填成的皮肤，口内就是嘴线那块面积填的暖色 ——
    它们与源层必然完全重合。质量门按"谁盖住谁"判缺陷，认不出这条关系就会把
    自己的正确输出判成不合格（实测 5 条，正好占满 ``MAX_DEFECTS`` 门槛）。
    """

    def test_closed_eye_image_covering_the_eye_is_a_note_not_a_defect(self):
        parts = clean_parts()
        parts.pop("eyes_left")
        # 真实分层里眼白与虹膜是**两块不相交**的区域（实测眼白 166 px、
        # 虹膜 236 px，交集 0），闭眼图是两者并集那 402 px。
        # 放在脸外的空白处，免得与夹具里别的部件相交。
        parts["eyewhite_left"] = rect(10, 150, 28, 162)
        parts["irides_left"] = rect(29, 150, 47, 162)
        parts["eyelid_left"] = rect(10, 150, 47, 162)

        bad, notes = LQ.check_duplicates(parts)

        assert bad == [], f"合成层被误报成越界扩张: {bad}"
        assert any("eyelid_left" in note for note in notes)

    def test_mouth_cavity_covering_the_mouth_is_a_note_not_a_defect(self):
        parts = clean_parts()
        parts["mouth_cavity"] = rect(85, 82, 115, 94)   # 与嘴线层完全重合

        bad, notes = LQ.check_duplicates(parts)

        assert bad == [], f"口内层被误报成越界扩张: {bad}"
        assert any("mouth_cavity" in note for note in notes)

    def test_a_real_over_extension_is_still_reported(self):
        """豁免只认「合成层 ↔ 它取自的层」这一对；真的越界扩张照报不误。"""
        parts = clean_parts()
        parts["eyelid_left"] = rect(78, 50, 96, 62)
        # 闭眼图盖住了**嘴线** —— 不是它的源层，是真越界
        parts["eyelid_left"] = rect(78, 50, 115, 94)

        bad, _notes = LQ.check_duplicates(parts)

        assert bad, "闭眼图盖住嘴线这种真越界必须报出来"


class TestSelftest:
    def test_module_selftest_reports_no_false_alarms(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = LQ.selftest()

        assert code == 0, buf.getvalue()
        assert "干净集 0 缺陷" in buf.getvalue()


@pytest.mark.parametrize("part", ["eyes_left", "eyes_right"])
def test_required_eye_parts_are_side_aware(part):
    parts = clean_parts()
    parts.pop(part)

    assert LQ.check_required(parts), f"缺 {part} 时必须报缺陷"
