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


class TestComponents:
    def test_components_return_bbox_masks_in_area_order(self):
        mask = np.zeros((100, 120), dtype=bool)
        mask[10:30, 15:35] = True
        mask[70:75, 90:95] = True

        components = SemanticLayerer._components(mask)

        assert [area for _, area, _, _ in components] == [400, 25]
        first, area, cx, cy = components[0]
        assert first.shape == mask.shape
        assert first._local.shape == (20, 20)
        assert np.asarray(first)[10:30, 15:35].all()
        assert (cx, cy) == pytest.approx((24.5, 19.5))
        second = components[1][0]
        assert second.shape == mask.shape
        assert second._local.shape == (5, 5)
        assert np.asarray(second)[70:75, 90:95].all()

    @staticmethod
    def _legacy_components():
        import ast
        import subprocess

        from core.segment_engine import semantic

        source = subprocess.check_output(
            ["git", "show", "5097b418:core/segment_engine/semantic.py"],
            cwd=__import__("pathlib").Path(__file__).resolve().parents[2],
        ).decode("utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SemanticSegmenter")
        function = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_components")
        assert any(
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "labels"
            for node in ast.walk(function)
        )
        function.decorator_list = []
        namespace = dict(vars(semantic))
        exec(compile(ast.Module(body=[function], type_ignores=[]), "legacy_components", "exec"), namespace)
        return namespace["_components"]

    @staticmethod
    def _storage_bytes(components):
        arrays = {}
        for comp, _, _, _ in components:
            values = [comp] if isinstance(comp, np.ndarray) else vars(comp).values()
            for value in values:
                if isinstance(value, np.ndarray):
                    while isinstance(value.base, np.ndarray):
                        value = value.base
                    arrays[id(value)] = value
        return sum(value.nbytes for value in arrays.values())

    @staticmethod
    def _fragmented_mask(layout):
        mask = np.zeros((256, 256), dtype=bool)
        if layout == "grid":
            mask[1::16, 1::16] = True
        else:
            for inset in range(1, 125, 3):
                end = mask.shape[0] - inset
                mask[inset, inset:end] = True
                mask[end - 1, inset:end] = True
                mask[inset:end, inset] = True
                mask[inset:end, end - 1] = True
        return mask

    @pytest.mark.parametrize("shared", [False, True])
    def test_numpy_copy_contract(self, shared):
        mask = self._fragmented_mask("rings") if shared else np.zeros((12, 14), dtype=bool)
        if not shared:
            mask[2:5, 3:7] = True
        comp = SemanticLayerer._components(mask)[0][0]
        expected = np.asarray(comp)

        for dtype in (None, bool, np.uint8):
            for copy in (None, True):
                actual = np.array(comp, dtype=dtype, copy=copy)
                np.testing.assert_array_equal(actual, expected)
                assert actual.dtype == np.dtype(bool if dtype is None else dtype)
                actual[:] = 0
                np.testing.assert_array_equal(np.asarray(comp), expected)
        with pytest.raises(ValueError, match="copy"):
            np.array(comp, copy=False)
        copied = np.copy(comp)
        copied[:] = False
        np.testing.assert_array_equal(np.asarray(comp), expected)

    @pytest.mark.parametrize("shared", [False, True])
    def test_component_and_ndarray_boolean_operations(self, shared):
        mask = self._fragmented_mask("rings") if shared else np.zeros((20, 24), dtype=bool)
        if not shared:
            mask[2:6, 3:8] = True
            mask[12:15, 17:21] = True
        first, second = [item[0] for item in SemanticLayerer._components(mask)[:2]]
        left, right = np.asarray(first), np.asarray(second)
        expected = left | right
        selector = np.indices(mask.shape)[0] < 4

        np.testing.assert_array_equal(first & selector, left & selector)
        np.testing.assert_array_equal(selector & first, selector & left)
        np.testing.assert_array_equal(first & second, left & right)
        np.testing.assert_array_equal(first | second, expected)
        np.testing.assert_array_equal(first | selector, left | selector)
        np.testing.assert_array_equal(selector | first, selector | left)
        out = np.zeros_like(mask)
        alias = out
        out |= first
        out |= second
        assert out is alias
        np.testing.assert_array_equal(out, expected)
        alias = first
        first |= second
        np.testing.assert_array_equal(first, expected)
        np.testing.assert_array_equal(np.asarray(alias), left)
        for axis in (None, 0, 1, -1, -2):
            np.testing.assert_array_equal(alias.any(axis=axis), left.any(axis=axis))

    @pytest.mark.parametrize("layout", ["grid", "rings"])
    def test_fragment_storage_bound_and_legacy_red_evidence(self, layout):
        import tracemalloc

        mask = self._fragmented_mask(layout)
        measurements = []
        results = []
        for implementation in (SemanticLayerer._components, self._legacy_components()):
            tracemalloc.start()
            try:
                components = implementation(mask)
                retained, peak = tracemalloc.get_traced_memory()
                measurements.append((self._storage_bytes(components), retained, peak))
            finally:
                tracemalloc.stop()
            results.append(components)
        current, legacy = results
        assert len(current) == len(legacy) > 10
        rebuilt = np.zeros_like(mask)
        for actual, expected in zip(current, legacy):
            assert actual[1:] == pytest.approx(expected[1:])
            np.testing.assert_array_equal(np.asarray(actual[0]), expected[0])
            rebuilt |= actual[0]
        np.testing.assert_array_equal(rebuilt, mask)
        bound = mask.size * np.dtype(np.int32).itemsize
        assert measurements[0][0] <= bound
        with pytest.raises(AssertionError):
            assert measurements[1][0] <= bound
        assert measurements[1][0] == len(legacy) * mask.nbytes
        assert measurements[0][2] < measurements[1][2]
        print(f"{layout}: storage/retained/peak current={measurements[0]} legacy={measurements[1]} bound={bound}")

    def test_face_base_merges_components_like_legacy(self, monkeypatch):
        skin = np.zeros((200, 200), dtype=bool)
        skin[80:125, 60:140] = True
        skin[15:25, 80:120] = True
        features = np.zeros_like(skin)
        fg = np.ones_like(skin)
        original = SemanticLayerer._components
        calls = []

        def tracked(mask):
            components = original(mask)
            calls.append(len(components))
            return components

        monkeypatch.setattr(SemanticLayerer, "_components", staticmethod(tracked))
        actual, chin = SemanticLayerer._face_base(skin, features, _anchors(), fg, skin.shape)
        assert max(calls) >= 2
        monkeypatch.setattr(SemanticLayerer, "_components", staticmethod(self._legacy_components()))
        expected, expected_chin = SemanticLayerer._face_base(skin, features, _anchors(), fg, skin.shape)
        np.testing.assert_array_equal(actual, expected)
        assert chin == expected_chin
        assert actual[15:25, 80:120].all()

    def test_fallback_callers_match_legacy(self, monkeypatch):
        from PIL import Image

        rgb = np.full((200, 200, 3), (40, 50, 80), dtype=np.uint8)
        rgb[30:125, 60:140] = SKIN
        rgb[70:150, 15:30] = SKIN
        rgb[145:195, 80:95] = SKIN
        rgb[62:70, 78:92] = (30, 40, 90)
        rgb[62:70, 108:122] = (30, 40, 90)
        rgb[84:87, 99:102] = (120, 105, 95)
        rgb[104:106, 91:110] = (125, 108, 96)
        image = Image.fromarray(rgb)
        fg = np.ones(rgb.shape[:2], dtype=bool)
        monkeypatch.setattr(face_detect, "detect_face", lambda *args: _anchors())
        segmenter = SemanticLayerer.__new__(SemanticLayerer)
        actual = segmenter._fallback_color_segment(image, fg)
        monkeypatch.setattr(SemanticLayerer, "_components", staticmethod(self._legacy_components()))
        expected = segmenter._fallback_color_segment(image, fg)
        assert actual.keys() == expected.keys()
        for name in expected:
            np.testing.assert_array_equal(actual[name], expected[name])

    def test_empty_and_no_cv2_fallback(self, monkeypatch):
        from core.segment_engine import semantic

        mask = np.zeros((8, 10), dtype=bool)
        assert SemanticLayerer._components(mask) == []
        mask[1:3, 2:5] = True
        monkeypatch.setattr(semantic, "HAS_CV2", False)
        components = SemanticLayerer._components(mask)
        assert len(components) == 1
        np.testing.assert_array_equal(components[0][0], mask)
        assert components[0][1:] == pytest.approx((6, 3.0, 1.5))


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


class TestSkinFaceSelection:
    """肤色兜底不能把**腿**当成脸。

    旧实现取"画面上 65% 内面积最大的肤色块"，全身立绘上那是腿（被 65%
    横线截断后 bbox 仍偏方），脸框于是落到大腿上：实测脸层出现在大腿、
    白衬衫被整片并进 hair_back，质量门报 7 条缺陷。
    """

    @staticmethod
    def _canvas(h=400, w=200):
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        rgb[...] = (45, 60, 82)
        rgb[40:120, 65:135] = SKIN    # 脸：偏方、靠上（面积更小）
        rgb[260:390, 55:145] = SKIN   # 腿：面积更大、位置更低
        return rgb

    def test_box_lands_on_the_face_not_the_legs(self):
        anchors = face_detect.detect_face_skin(self._canvas())

        assert anchors is not None
        x0, y0, x1, y1 = anchors.box
        assert y1 < 200, f"脸框落到下半身（y={y0}..{y1}），说明选中了腿"
        assert 60 <= x0 <= 70 and 130 <= x1 <= 140

    def test_component_picker_returns_none_without_a_face_shaped_blob(self):
        # 只有两条细长的手臂：宽高比超出脸的范围，不能硬指一个
        skin = np.zeros((400, 200), dtype=bool)
        skin[100:380, 20:50] = True
        skin[100:380, 150:180] = True

        assert face_detect._skin_face_component(skin) is None
