"""Tests for the anchor-driven facial feature extraction.

背景：旧实现用「脸的肤色区域 & 非肤色」找五官，眼睛和嘴在肤色掩码里是
不封闭的洞，填洞填不上，实测五官只剩 19 像素、整层消失。现在改为
用 YuNet 的 5 点关键点定位 + 「上下被皮肤夹住的非肤色段」提五官，本文件
锁定该行为，防止退回到靠位置猜的老路上。
"""

import numpy as np
import pytest

from core.segment_engine import face_detect
from core.segment_engine.semantic import SemanticLayerer

SKIN = (252, 224, 205)


def _anchors(**over):
    base = dict(
        box=(40, 20, 160, 140),
        right_eye=(85.0, 65.0),
        left_eye=(115.0, 65.0),
        nose=(100.0, 85.0),
        right_mouth=(92.0, 105.0),
        left_mouth=(108.0, 105.0),
        source=face_detect.SOURCE_YUNET,
    )
    base.update(over)
    return face_detect.FaceAnchors(**base)


def _skin_canvas(h=200, w=200):
    rgb = np.empty((h, w, 3), dtype=np.uint8)
    rgb[...] = SKIN
    return rgb


class TestFaceAnchors:
    def test_estimate_from_box_keeps_canonical_geometry(self):
        anchors = face_detect.estimate_anchors_from_box((100, 50, 200, 170))

        assert anchors.source == face_detect.SOURCE_SKIN
        assert anchors.right_eye[0] < anchors.left_eye[0]
        assert anchors.box[0] <= anchors.nose[0] <= anchors.box[2]
        # 鼻在眼下、嘴在鼻下
        assert anchors.right_eye[1] < anchors.nose[1] < anchors.right_mouth[1]

    def test_derived_midpoints_and_eye_distance(self):
        anchors = _anchors()

        assert anchors.eye_mid == pytest.approx((100.0, 65.0))
        assert anchors.mouth_mid == pytest.approx((100.0, 105.0))
        assert anchors.eye_distance == pytest.approx(30.0)

    def test_detect_face_returns_anchors_or_none(self):
        rgb = _skin_canvas()
        found = face_detect.detect_face(rgb)

        # 纯色画面上应当检测不到脸；一旦检测到就必须带关键点与来源标记
        if found is not None:
            assert isinstance(found, face_detect.FaceAnchors)
            assert found.source in (face_detect.SOURCE_YUNET, face_detect.SOURCE_SKIN)


class TestBoundedRuns:
    def test_segment_between_skin_is_kept(self):
        h, w = 20, 5
        non_skin = np.zeros((h, w), dtype=bool)
        non_skin[8:12, :] = True
        skin_tol = ~non_skin

        out = SemanticLayerer._bounded_runs(non_skin, skin_tol, 0, 4, 0, 19, 10.0, 6.0)

        assert out[8:12].all()
        assert not out[:8].any() and not out[12:].any()

    def test_column_that_never_touches_skin_is_dropped(self):
        """整列都是非肤色（= 垂下来的发丝），上下接不到皮肤，必须丢掉。"""
        h, w = 20, 5
        non_skin = np.zeros((h, w), dtype=bool)
        non_skin[:, 2] = True
        skin_tol = ~non_skin

        out = SemanticLayerer._bounded_runs(non_skin, skin_tol, 0, 4, 0, 19, 10.0, 30.0)

        assert not out.any()

    def test_run_taller_than_budget_is_dropped(self):
        h, w = 40, 3
        non_skin = np.zeros((h, w), dtype=bool)
        non_skin[10:35, :] = True
        skin_tol = ~non_skin

        out = SemanticLayerer._bounded_runs(non_skin, skin_tol, 0, 2, 0, 39, 20.0, 6.0)

        assert not out.any()


class TestFeatureParts:
    @staticmethod
    def _ink_with_eyes():
        """肤色画布 + 两块被皮肤包住的眼睛 + 鼻子/嘴的墨点。"""
        rgb = _skin_canvas()
        ink = np.zeros(rgb.shape[:2], dtype=bool)
        for cx in (85, 115):
            rgb[62:70, cx - 7 : cx + 7] = (30, 40, 90)
            ink[62:70, cx - 7 : cx + 7] = True
        rgb[84:87, 99:102] = (120, 105, 95)
        ink[84:87, 99:102] = True
        rgb[104:106, 91:110] = (125, 108, 96)
        ink[104:106, 91:110] = True
        return rgb, ink

    def test_eyes_are_split_per_side_and_exclude_the_far_eye(self):
        rgb, ink = self._ink_with_eyes()
        skin_tol = ~ink
        h, w = rgb.shape[:2]

        left, right = SemanticLayerer._eye_parts(_anchors(), skin_tol, np.ones((h, w), bool), (h, w))

        assert left.any() and right.any()
        ys_l, xs_l = np.nonzero(left)
        ys_r, xs_r = np.nonzero(right)
        assert xs_l.max() < 100 < xs_r.min()
        # 覆盖到眼睛所在行
        assert 62 <= ys_l.min() and ys_l.max() <= 70
        assert 62 <= ys_r.min() and ys_r.max() <= 70

    def test_nose_stays_in_its_small_window(self):
        rgb, ink = self._ink_with_eyes()
        h, w = rgb.shape[:2]

        nose = SemanticLayerer._nose_part(_anchors(), ink, (h, w))

        assert nose.any()
        ys, xs = np.nonzero(nose)
        assert 84 <= ys.min() and ys.max() <= 87
        assert 99 <= xs.min() and xs.max() <= 101

    def test_mouth_keeps_the_horizontal_stroke_only(self):
        rgb, ink = self._ink_with_eyes()
        h, w = rgb.shape[:2]

        mouth = SemanticLayerer._mouth_part(_anchors(), ink, (h, w))

        assert mouth.any()
        ys, xs = np.nonzero(mouth)
        assert 104 <= ys.min() and ys.max() <= 105
        assert xs.min() >= 91 and xs.max() < 110

    def test_mouth_rejects_vertical_stroke(self):
        """竖条（脸颊线/发丝）落在嘴的窗口里也不能被当成嘴。"""
        rgb = _skin_canvas()
        ink = np.zeros(rgb.shape[:2], dtype=bool)
        ink[100:112, 100:102] = True
        h, w = rgb.shape[:2]

        mouth = SemanticLayerer._mouth_part(_anchors(), ink, (h, w))

        assert not mouth.any()

    def test_missing_anchor_scale_yields_empty_parts(self):
        """关键点退化（间距 < 2px）时不能瞎画，返回空掩码。"""
        degenerate = _anchors(right_eye=(100.0, 65.0), left_eye=(100.5, 65.0))
        rgb, ink = self._ink_with_eyes()
        h, w = rgb.shape[:2]

        assert not SemanticLayerer._nose_part(degenerate, ink, (h, w)).any()
        assert not SemanticLayerer._mouth_part(degenerate, ink, (h, w)).any()
