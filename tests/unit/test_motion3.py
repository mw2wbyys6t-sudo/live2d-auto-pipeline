"""motion3.json 生成器的结构不变量。

「能不能被内核播放」由 tests/integration/test_moc3_motion_acceptance.py 用真实
运行时判定；这里只钉住文件自身的算术：段布局与 Meta 自洽、扫满键形区间、
可无缝循环，以及官方样例实测出来的那条写法约束（带缩进，不能压单行）。

这里的段解码是测试自己写的一份，故意不复用产物代码 —— 否则「编码错 + 解码
跟着错」会在测试里互相掩护。
"""
import json

import pytest

from live2d_builder.motion.motion3 import Motion3Builder

# 段类型 -> 点数（官方 Haru 六个动作文件与 Meta 计数 6/6 对账实测）
SEGMENT_POINTS = {0: 1, 1: 3, 2: 1, 3: 1}


def decode(segments):
    """[t0, v0] + (类型 + 点) -> [(类型, [(t, v), ...]), ...]。"""
    result = []
    i = 2
    while i < len(segments):
        seg_type = segments[i]
        assert seg_type in SEGMENT_POINTS, f"未知段类型 {seg_type!r}"
        count = SEGMENT_POINTS[seg_type]
        flat = segments[i + 1: i + 1 + 2 * count]
        assert len(flat) == 2 * count, "段被截断"
        result.append((seg_type, [(flat[k], flat[k + 1])
                                  for k in range(0, 2 * count, 2)]))
        i += 1 + 2 * count
    return result


def points_of(curve):
    """曲线的 (时间, 值) 端点序列：初始点 + 每段终点。"""
    segments = curve["Segments"]
    return [(segments[0], segments[1])] + [p[-1] for _t, p in decode(segments)]


def test_segments_are_type_prefixed_linear_triples():
    """每段以类型 0（线性）开头，段长 3 浮点 —— 官方布局。"""
    doc = Motion3Builder(duration=6.0, fps=30.0).build_idle(
        {"ParamA": [-30.0, 0.0, 30.0]})
    segments = doc["Curves"][0]["Segments"]
    assert len(segments) == 2 + 3 * 180
    assert all(entry[0] == 0 for entry in decode(segments))
    times = [t for t, _v in points_of(doc["Curves"][0])]
    assert times[0] == 0.0
    assert times[1] == pytest.approx(1 / 30, abs=1e-6)
    assert times[2] == pytest.approx(2 / 30, abs=1e-6)


def test_curve_spans_the_keyed_range_exactly():
    doc = Motion3Builder(duration=6.0).build_idle({"ParamA": [-30.0, 0.0, 30.0]})
    values = [v for _t, v in points_of(doc["Curves"][0])]
    assert max(values) == pytest.approx(30.0)
    assert min(values) == pytest.approx(-30.0)


def test_curve_is_seamlessly_loopable():
    doc = Motion3Builder(duration=6.0).build_idle({"ParamA": [-30.0, 0.0, 30.0]})
    points = points_of(doc["Curves"][0])
    assert points[0][1] == pytest.approx(points[-1][1])
    assert points[0][0] == 0.0 and points[-1][0] == pytest.approx(6.0)


def test_asymmetric_range_never_leaves_the_keyed_interval():
    """嘴部开合这类 0..1 参数不能被打到负值。"""
    doc = Motion3Builder().build_idle({"ParamMouthOpenY": [0.0, 1.0]})
    values = [v for _t, v in points_of(doc["Curves"][0])]
    assert min(values) >= -1e-9 and max(values) <= 1.0 + 1e-9


def test_rest_value_is_the_key_closest_to_zero():
    assert Motion3Builder.rest_value([-30.0, 0.0, 30.0]) == 0.0
    assert Motion3Builder.rest_value([0.2, 0.8]) == 0.2


def test_meta_counts_are_self_consistent_with_curves():
    """Meta 的 P/S 必须与解码结果一致：内核按这两个数分配缓冲，错了就是崩。"""
    doc = Motion3Builder(duration=4.0).build_idle(
        {"ParamA": [-10.0, 0.0, 10.0], "ParamB": [0.0, 1.0]})
    segments = points = 0
    for curve in doc["Curves"]:
        decoded = decode(curve["Segments"])
        segments += len(decoded)
        points += 1 + sum(len(p) for _t, p in decoded)
    assert doc["Meta"]["CurveCount"] == 2
    assert doc["Meta"]["TotalPointCount"] == points
    assert doc["Meta"]["TotalSegmentCount"] == segments
    assert doc["Meta"]["Duration"] == 4.0
    assert doc["Meta"]["Loop"] is True
    # floats = 2P + S 是官方 6 个文件逐项对账成立的恒等式
    total_floats = sum(len(c["Segments"]) for c in doc["Curves"])
    assert total_floats == (2 * doc["Meta"]["TotalPointCount"]
                            + doc["Meta"]["TotalSegmentCount"])


def test_no_drivable_parameter_produces_no_document():
    """没有可动参数就不该产出动画文件（否则会造出「有文件但一动不动」的假产物）。"""
    assert Motion3Builder().build_idle({}) is None
    assert Motion3Builder().build_idle({"ParamA": [0.0]}) is None
    assert Motion3Builder().build_idle({"ParamA": [5.0, 5.0]}) is None


def test_exported_file_is_indented_like_the_editor(tmp_path):
    """必须像 Cubism Editor 那样带缩进写：紧凑单行会被加载路径直接拒绝。

    证据见 tools/bisect_motion_encoding.py 与 tools/diag_extra_motion.py ——
    同一份内容 tab/2 空格缩进能加载并真的驱动参数，紧凑单行（含「逗号后换行
    但无缩进」）一律 Load extra motion failed。官方文件本身就是 tab 缩进。
    """
    doc = Motion3Builder().build_idle({"ParamA": [-30.0, 0.0, 30.0]})
    manifest = Motion3Builder().export_to_directory(
        str(tmp_path), "char", {"idle": doc})
    entry = manifest["idle"][0]
    path = tmp_path / entry["File"]
    text = path.read_text(encoding="utf-8")
    assert "\n\t" in text, "motion 文件必须带缩进换行，不能压成单行"
    assert json.loads(text)["Meta"]["CurveCount"] == 1
    # 淡入淡出走 Editor 默认值，与官方 Haru 的 model3.json 写法一致
    assert entry["FadeInTime"] == 0.5 and entry["FadeOutTime"] == 0.5


def test_invalid_builder_parameters_are_rejected():
    with pytest.raises(ValueError):
        Motion3Builder(fps=0)
    with pytest.raises(ValueError):
        Motion3Builder(duration=-1)
