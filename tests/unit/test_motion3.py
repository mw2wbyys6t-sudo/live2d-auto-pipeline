"""motion3.json 生成器的结构不变量。

「能不能被内核播放」由 tests/integration/test_moc3_motion_acceptance.py 用真实
运行时判定；这里只钉住文件自身的算术：段布局与 Meta 自洽、行程与幅度落在键形
区间内、可无缝循环，以及官方样例实测出来的那条写法约束（带缩进，不能压单行）。

这里的段解码是测试自己写的一份，故意不复用产物代码 —— 否则「编码错 + 解码
跟着错」会在测试里互相掩护。
"""
import json
import re

import pytest

from live2d_builder.motion.motion3 import Motion3Builder, dumps_motion

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


def test_full_range_sweeps_the_keyed_range_exactly():
    """扫掠诊断模式（内核验收用）：曲线必须打到键形两端。"""
    doc = Motion3Builder(duration=6.0, full_range=True).build_idle(
        {"ParamA": [-30.0, 0.0, 30.0]})
    values = [v for _t, v in points_of(doc["Curves"][0])]
    assert max(values) == pytest.approx(30.0)
    assert min(values) == pytest.approx(-30.0)


def test_default_idle_moves_but_stays_well_inside_the_keyed_range():
    """出厂 idle 要动，但只走一小段 —— 全行程播放实测像抽搐。"""
    keys = {"ParamAngleZ": [-30.0, 0.0, 30.0], "ParamMouthOpenY": [0.0, 0.5, 1.0]}
    doc = Motion3Builder(duration=6.0).build_idle(keys)
    for curve in doc["Curves"]:
        values = [v for _t, v in points_of(curve)]
        low, high = min(keys[curve["Id"]]), max(keys[curve["Id"]])
        assert max(values) - min(values) > 1e-2, f"{curve['Id']} 一动不动"
        assert low - 1e-9 <= min(values) and max(values) <= high + 1e-9
    angle = next(c for c in doc["Curves"] if c["Id"] == "ParamAngleZ")
    values = [v for _t, v in points_of(angle)]
    # 转头幅度按语义收窄：±30° 的行程只走其中一小部分
    assert max(abs(v) for v in values) <= 30.0 * 0.30


def test_symmetric_pairs_share_one_waveform():
    """左右眼睑必须同步：两条相位不同的曲线看起来就是在抽动。"""
    keys = {"ParamEyeLOpen": [0.0, 1.0], "ParamEyeROpen": [0.0, 1.0]}
    defaults = {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}
    doc = Motion3Builder().build_idle(keys, defaults=defaults)
    segments = {c["Id"]: c["Segments"] for c in doc["Curves"]}
    assert segments["ParamEyeLOpen"] == segments["ParamEyeROpen"]


def test_parameters_with_same_key_range_get_distinct_waveforms():
    """回归：ParamBreath 与 ParamMouthOpenY 曾生成逐值相同的曲线，读起来像喘气。

    两者键形都是 [0, 0.5, 1]、排序索引同余 4，所以除了相位还要有幅度差异，
    否则任何一组「同键形 + 同索引」的参数都会同步摆动。
    """
    doc = Motion3Builder().build_idle({
        "ParamBreath": [0.0, 0.5, 1.0],
        "ParamMouthOpenY": [0.0, 0.5, 1.0],
    })
    by_id = {c["Id"]: c["Segments"] for c in doc["Curves"]}
    assert by_id["ParamBreath"] != by_id["ParamMouthOpenY"]


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


def test_rest_pose_comes_from_the_parameter_declaration():
    """眼睑的静止值是 1.0（睁开）；按「离 0 最近的键」会取到 0.0，待机一直闭眼。"""
    doc = Motion3Builder().build_idle(
        {"ParamEyeLOpen": [0.0, 1.0]}, defaults={"ParamEyeLOpen": 1.0})
    values = [v for _t, v in points_of(doc["Curves"][0])]
    assert min(values) >= 0.8 - 1e-9 and max(values) <= 1.0 + 1e-9
    # 没有声明默认值时才退回离 0 最近的键（对以 0 为静止值的参数成立）
    fallback = Motion3Builder().build_idle({"ParamEyeLOpen": [0.0, 1.0]})
    values = [v for _t, v in points_of(fallback["Curves"][0])]
    assert max(values) <= 0.2 + 1e-9


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


def test_tiny_values_never_serialize_with_exponent_notation():
    """官方内核的 JSON 解析器不认 ``5.3e-05`` —— 实测整份 motion 被拒。

    待机幅度收窄后曲线必然出现 <1e-4 的值；``json.dumps`` 会对它们输出指数
    写法，内核随即 ``Json parse error``，参数一动不动。序列化层必须展开。
    """
    document = {"a": [5.3e-05, -2.0e-06, 0.0, 1.0]}
    text = dumps_motion(document)
    assert json.loads(text) == document
    assert re.search(r"\d[eE][+-]\d", text) is None, text
    assert "0.000053" in text and "-0.000002" in text


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
    # 出厂 idle 的采样值里有大量接近 0 的小数，一个指数写法就足以让内核拒收
    assert re.search(r"\d[eE][+-]\d", text) is None
    # 淡入淡出走 Editor 默认值，与官方 Haru 的 model3.json 写法一致
    assert entry["FadeInTime"] == 0.5 and entry["FadeOutTime"] == 0.5


def test_invalid_builder_parameters_are_rejected():
    with pytest.raises(ValueError):
        Motion3Builder(fps=0)
    with pytest.raises(ValueError):
        Motion3Builder(duration=-1)
