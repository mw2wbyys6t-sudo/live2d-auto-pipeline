"""warp 变形器编译：顶点换算成 (s, t)、additional 段补齐、非法参考网格要拒绝。

这里的每条结构断言都对应一次官方 Cubism Core 实测（见
tests/integration/test_moc3_deformer_acceptance.py 与 tools/probe_warp_st_identity.py）。
"""
import base64
import math
from pathlib import Path

import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
    KeyformShape,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    RotationDeformerSpec,
    RotationKeyform,
    UnsupportedRig,
    WarpDeformerSpec,
    compile_static_rig,
    deformer_space_map,
    reference_rect,
    to_grid_st,
)

PPU = 100.0

# Task 1.5 的黄金字节夹具：根级（无父变形器）全产物字节，由**改动前**的编译器生成。
# 落成 base64 文本而不是 .bin —— 仓库 .gitignore 的 `*.bin` 是权重文件规则，会连带
# 把测试用的二进制挡在库外。
_GOLDEN_FLAT_WARP_BYTES_FILE = (
    Path(__file__).resolve().parent / "fixtures"
    / "moc3_root_level_flat_warps_golden.b64")


def _golden_flat_warp_bytes() -> bytes:
    return base64.b64decode(_GOLDEN_FLAT_WARP_BYTES_FILE.read_text(encoding="ascii"))


def _square(name, x0=0.0, y0=0.0, side=100.0):
    verts = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    return MeshSpec(
        mesh_id=name, vertices=verts, triangles=[(0, 2, 1), (0, 3, 2)],
        uvs=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        deformer_id="Warp1")


def _lattice(n=3, x0=0.0, y0=0.0, x1=100.0, y1=100.0, key_value=0.0, dx=0.0):
    step_x = (x1 - x0) / (n - 1)
    step_y = (y1 - y0) / (n - 1)
    points = [(x0 + c * step_x + dx, y0 + r * step_y)
              for r in range(n) for c in range(n)]
    return DeformerGrid(key_value, points)


def _spec(grids, parameter=None, meshes=None):
    deformer = WarpDeformerSpec(deformer_id="Warp1", rows=2, cols=2, grids=grids,
                                parameter_id=parameter.parameter_id if parameter
                                else "")
    params = [parameter] if parameter else []
    return RigSpec(meshes=meshes if meshes is not None else [_square("ArtMesh1")],
                   parameters=params, deformers=[deformer],
                   pixels_per_unit=PPU)


def _pool_positions(doc, begin, count):
    xs = doc.get("keyform_position.xys")
    chunk = xs[begin:begin + 2 * count]
    return list(zip(chunk[0::2], chunk[1::2]))


def _assert_points(actual, expected, **kwargs):
    """逐点比较二维点列表。

    不用 ``pytest.approx([(x, y), ...])``：``approx`` 对**嵌套结构**的支持
    随 pytest 版本变化，部分版本上直接抛
    ``TypeError: pytest.approx() does not support nested data structures``
    （实测 CI 与本地版本行为不一致）。逐点比较在所有版本上一致。
    """
    assert len(actual) == len(expected), (
        f"点数不符：实际 {len(actual)} != 期望 {len(expected)}（{actual}）")
    for index, (got, want) in enumerate(zip(actual, expected)):
        assert got == pytest.approx(want, **kwargs), (
            f"第 {index} 个点不符：实际 {got} != 期望 {want}")


def test_static_warp_stores_vertices_as_grid_coordinates():
    doc = compile_static_rig(_spec([_lattice()]))
    begin = doc.get("art_mesh_keyform.keyform_position_begin_indices")[0]
    # (s, t) 无量纲：不除 pixels_per_unit，且矩形参考网格下就是线性归一化
    _assert_points(_pool_positions(doc, begin, 4),
                   [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    # 控制网格本身仍在模型空间（除以 ppu）
    grid_begin = doc.get(
        "warp_deformer_keyform.keyform_position_begin_indices")[0]
    points = _pool_positions(doc, grid_begin, 9)
    assert points[0] == pytest.approx((0.0, 0.0))
    assert points[-1] == pytest.approx((1.0, 1.0))
    assert doc.get("art_mesh.parent_deformer_indices") == [0]
    assert doc.get("deformer.types") == [0]
    assert doc.get("warp_deformer.rows") == [2]
    assert doc.get("warp_deformer.vertex_counts") == [9]


def test_non_empty_deformer_table_needs_additional_section():
    """V3_03 下 deformer 表非空时，编译器必须写出 additional 段。"""
    doc = compile_static_rig(_spec([_lattice()]))
    assert doc.get("additional.quad_transforms") == [0]
    assert lint_document(doc) == []


def test_reference_rect_is_the_default_keyform():
    deformer = WarpDeformerSpec(
        "Warp1", 2, 2,
        [_lattice(key_value=-30.0, dx=5.0), _lattice(key_value=0.0),
         _lattice(key_value=30.0, dx=-5.0)],
        parameter_id="Param0")
    rect = reference_rect(deformer, {"Param0": ParameterSpec("Param0", default=0.0)})
    assert rect == pytest.approx((0.0, 0.0, 100.0, 100.0))
    _assert_points(to_grid_st([(25.0, 50.0)], rect), [(0.25, 0.5)])


def test_multi_keyform_warp_needs_a_keyform_at_parameter_default():
    spec = _spec([_lattice(key_value=-30.0), _lattice(key_value=30.0)],
                 parameter=ParameterSpec("Param0", default=0.0))
    with pytest.raises(UnsupportedRig, match="参数默认值"):
        compile_static_rig(spec)


def test_curved_rest_grid_is_refused():
    """静止形不是轴对齐规则点阵时，线性 (s, t) 换算不成立，必须拒绝。"""
    bent = _lattice()
    points = list(bent.points)
    points[4] = (50.0, 60.0)          # 中心点偏离行列网格
    spec = _spec([DeformerGrid(bent.key_value, points)])
    with pytest.raises(UnsupportedRig, match="轴对齐规则点阵"):
        compile_static_rig(spec)


def test_degenerate_rest_grid_is_refused():
    flat = DeformerGrid(0.0, [(50.0, 50.0)] * 9)
    with pytest.raises(UnsupportedRig, match="退化"):
        compile_static_rig(_spec([flat]))


def test_deformer_count_without_specs_still_refused():
    """管线只报数量、没有网格数据时也要拒绝（不静默降级为静态）。"""
    with pytest.raises(UnsupportedRig, match="变形器数量与规格不符"):
        compile_static_rig(RigSpec(meshes=[_square("A", x0=0.0)],
                                   deformer_count=3))


# ----------------------------------------------------------------------
# 表情形变：官方表情参数驱动 warp 控制网格（单轴 / 多轴带）
# ----------------------------------------------------------------------

from live2d_builder.exporter.moc3_pipeline import (  # noqa: E402
    EYE_CLOSED_SCALE,
    HEAD_DEPTH_SPANS,
    HEAD_SHIFT_X,
    HEAD_SHIFT_Y,
    MOUTH_CAVITY_STRETCH,
    MOUTH_OPEN_STRETCH,
    SHOULDER_LIFT,
    TORSO_LEAN_X,
    build_deformers,
    to_model_xy,
    _head_transform,
    driven_parameters,
    _attach_capabilities,
    _band_indices,
    _centered_fraction,
    _opacity_keyforms,
)


def _patch(name, x0=0.0, y0=0.0, x1=40.0, y1=10.0):
    """3×2 面片（与 MeshGenerator 给小部件的形状一致）。"""
    xm = (x0 + x1) / 2.0
    return MeshSpec(
        mesh_id=name,
        vertices=[(x0, y0), (xm, y0), (x1, y0), (x0, y1), (xm, y1), (x1, y1)],
        triangles=[(0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4)],
        uvs=[(0.0, 0.0)] * 6,
    )


def _entry(name, parameters, profile, targets, rows=2, cols=3, mirror=False,
           under=""):
    out = {"name": name, "type": "warp", "parent": "Mouth",
           "grid_rows": rows, "grid_cols": cols,
           "parameters": list(parameters), "profile": profile,
           "targets": list(targets)}
    if mirror:
        out["mirror"] = True
    if under:
        out["under"] = under
    return out


def _build(meshes, entries, params):
    return build_deformers(meshes, entries, 200.0, 200.0,
                           {p.parameter_id: p for p in params}, {})


MOUTH_PARAMS = [
    ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0),
    ParameterSpec("ParamMouthForm", -1.0, 1.0, 0.0),
    ParameterSpec("ParamMouthSize", -1.0, 1.0, 0.0),
]


def _grid_by_keys(deformer, values):
    """多轴带里挑出「各轴都取给定值」的那一格。"""
    axes = deformer.axes_spec
    index = 0
    stride = 1
    for (pid, keys), want in zip(axes, values):
        slot = min(range(len(keys)), key=lambda i: abs(float(keys[i]) - want))
        index += slot * stride
        stride *= len(keys)
    return deformer.grids[index]


class TestExpressionDeformerProfiles:
    """表情形变必须：静止键 = 恒等变换、形变量按网格自身尺寸算、多参数合成一条带。

    历史读数：``ParamMouthOpenY`` / ``ParamEyeLOpen`` 在官方内核受控像素验证里
    ``changed_pixels`` 恒为 0 —— 参数声明了、没有任何键形或变形器绑定它们；
    更早一步它们是"绑了但只能绑一个"，口型/眉角之类的第二个参数完全没处放。
    """

    def test_mouth_band_is_identity_at_default_and_stretches_when_open(self):
        meshes = [_patch("mouth", 0.0, 0.0, 40.0, 10.0)]
        warps, _skipped, _static = _build(
            meshes,
            [_entry("MouthOpen",
                    ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
                    "mouth", ["mouth"])],
            MOUTH_PARAMS)

        assert len(warps) == 1
        deformer = warps[0]
        # 一条带同时驱动三个参数，键数之积 = 控制网格数
        assert [pid for pid, _keys in deformer.axes_spec] == [
            "ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"]
        assert len(deformer.grids) == 2 * 3 * 3

        rest = _grid_by_keys(deformer, [0.0, 0.0, 0.0])
        opened = _grid_by_keys(deformer, [1.0, 0.0, 0.0])
        # 静止形 = 恒等：控制网格就是 (网格包围盒 +5% 余量) 上的规则点阵
        assert sorted({round(p[0], 6) for p in rest.points}) == [-2.0, 20.0, 42.0]
        assert sorted({round(p[1], 6) for p in rest.points}) == [-0.5, 10.5]

        def y_at(grid, t):
            ys = sorted(p[1] for p in grid.points)
            return ys[0] + t * (ys[-1] - ys[0])

        rest_top, rest_bottom = min(p[1] for p in rest.points), max(p[1] for p in rest.points)
        t_top = (0.0 - rest_top) / (rest_bottom - rest_top)
        t_bottom = (10.0 - rest_top) / (rest_bottom - rest_top)
        assert y_at(opened, t_top) == pytest.approx(y_at(rest, t_top), abs=1e-6)
        assert y_at(opened, t_bottom) - y_at(rest, t_bottom) == \
            pytest.approx(MOUTH_OPEN_STRETCH * 10.0, abs=0.01)
        assert meshes[0].deformer_id == "MouthOpen"

    def test_mouth_form_bends_the_ends_but_not_the_middle(self):
        meshes = [_patch("mouth", 0.0, 0.0, 40.0, 10.0)]
        warps, _skipped, _static = _build(
            meshes,
            [_entry("MouthOpen",
                    ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
                    "mouth", ["mouth"])],
            MOUTH_PARAMS)
        deformer = warps[0]
        rest = _grid_by_keys(deformer, [0.0, 0.0, 0.0])
        smile = _grid_by_keys(deformer, [0.0, 1.0, 0.0])

        def by_x(grid):
            return {round(p[0], 3): p[1] for p in grid.points}

        before, after = by_x(rest), by_x(smile)
        xs = sorted(before)
        # 中间列不动、两端上抬（屏幕上方 = y 更小）
        assert after[xs[1]] == pytest.approx(before[xs[1]], abs=1e-6)
        assert after[xs[0]] < before[xs[0]] - 1.0
        assert after[xs[-1]] < before[xs[-1]] - 1.0

    def test_mouth_size_scales_horizontally_only(self):
        meshes = [_patch("mouth", 0.0, 0.0, 40.0, 10.0)]
        warps, _skipped, _static = _build(
            meshes,
            [_entry("MouthOpen",
                    ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
                    "mouth", ["mouth"])],
            MOUTH_PARAMS)
        deformer = warps[0]
        rest = _grid_by_keys(deformer, [0.0, 0.0, 0.0])
        wide = _grid_by_keys(deformer, [0.0, 0.0, 1.0])

        rest_xs = sorted({round(p[0], 3) for p in rest.points})
        wide_xs = sorted({round(p[0], 3) for p in wide.points})
        assert wide_xs[-1] - wide_xs[0] > rest_xs[-1] - rest_xs[0]
        assert [p[1] for p in wide.points] == pytest.approx([p[1] for p in rest.points])

    def test_eye_band_collapses_when_closed(self):
        meshes = [_patch("eye", 0.0, 0.0, 40.0, 20.0)]
        warps, _skipped, _static = _build(
            meshes,
            [_entry("EyeBlink_L", ["ParamEyeLOpen"], "eye", ["eye"], cols=2)],
            [ParameterSpec("ParamEyeLOpen", 0.0, 1.0, 1.0)])

        deformer = warps[0]
        assert deformer.parameter_id == "ParamEyeLOpen"
        closed, opened = deformer.grids
        open_span = max(p[1] for p in opened.points) - min(p[1] for p in opened.points)
        closed_span = max(p[1] for p in closed.points) - min(p[1] for p in closed.points)
        assert closed_span == pytest.approx(open_span * EYE_CLOSED_SCALE, rel=0.02)

    def test_missing_parameter_leaves_the_deformer_static(self):
        """profile 需要三个参数，模型只声明了两个：宁可不驱动，也不交半套形变。"""
        meshes = [_patch("mouth")]
        warps, skipped, _static = _build(
            meshes,
            [_entry("MouthOpen",
                    ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
                    "mouth", ["mouth"])],
            [ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0)])

        assert warps[0].parameter_id == ""
        assert len(warps[0].grids) == 1
        assert any("MouthOpen" in s for s in skipped)

    def test_unknown_profile_keeps_the_deformer_static(self):
        meshes = [_patch("mouth")]
        warps, skipped, _static = _build(
            meshes,
            [_entry("MouthOpen", ["ParamMouthOpenY"], "no_such_profile", ["mouth"])],
            MOUTH_PARAMS)

        assert warps[0].parameter_id == ""
        assert len(warps[0].grids) == 1
        assert any("MouthOpen" in s for s in skipped)

    def test_deformer_without_members_is_reported_not_silently_dropped(self):
        warps, skipped, _static = _build(
            [_patch("face")],
            [_entry("MouthOpen", ["ParamMouthOpenY"], "mouth", ["mouth"])],
            MOUTH_PARAMS)

        assert warps == []
        assert any("MouthOpen" in s for s in skipped)


class TestEyeCollapseAndCavityProfiles:
    """闭眼与口内两条带：眼球向上收拢，口内张得比嘴唇更深。

    闭眼**不是**"眼球被压扁"——压扁会把眼球压成一条横在眼睛中间的线，像眯眼。
    正确的样子是眼球整体**向上收拢**进上睫毛线里，于是画在眼球**下面**的
    「闭眼图」（皮肤 + 原画上睫毛）露出来。所以这里的判据是：收拢后的控制网格
    贴着网格**上沿**，而不是夹在中间。
    """

    def test_eye_band_collapses_upward_to_the_top_edge(self):
        meshes = [_patch("eyewhite_left", 0.0, 0.0, 40.0, 20.0)]
        warps, _skipped, _static = _build(
            meshes,
            [_entry("EyeBlink_L", ["ParamEyeLOpen"], "eye", ["eyewhite_left"],
                    cols=2)],
            [ParameterSpec("ParamEyeLOpen", 0.0, 1.0, 1.0)])

        assert len(warps) == 1
        deformer = warps[0]
        assert deformer.parameter_id == "ParamEyeLOpen"
        closed, opened = deformer.grids

        def span(grid):
            ys = [p[1] for p in grid.points]
            return min(ys), max(ys)

        closed_lo, closed_hi = span(closed)
        open_lo, open_hi = span(opened)
        # 收拢后只剩眼高的 EYE_CLOSED_SCALE
        assert closed_hi - closed_lo == pytest.approx(
            (open_hi - open_lo) * EYE_CLOSED_SCALE, rel=0.02)
        # 轴心在**上沿**：收拢后的一小条要贴在网格上沿，而不是缩在中间。
        # （用网格自身的上下沿比，不用控制网格的：控制网格带 5% 余量。）
        mesh_top, mesh_bottom = 0.0, 20.0
        assert closed_hi <= mesh_top + 0.15 * (mesh_bottom - mesh_top), (
            f"收拢后仍停在 {closed_hi:.2f}，没有贴住上沿 {mesh_top}")
        assert closed_hi < (open_lo + open_hi) / 2.0

    def test_mouth_cavity_opens_deeper_than_the_lips(self):
        meshes = [_patch("mouth", 0.0, 0.0, 40.0, 10.0),
                  _patch("mouth_cavity", 0.0, 0.0, 40.0, 10.0)]
        warps, _skipped, _static = _build(
            meshes,
            [_entry("MouthOpen",
                    ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
                    "mouth", ["mouth"]),
             _entry("MouthCavity", ["ParamMouthOpenY"], "mouth_cavity",
                    ["mouth_cavity"], cols=2)],
            MOUTH_PARAMS)

        by_name = {d.deformer_id: d for d in warps}
        lips = by_name["MouthOpen"]
        cavity = by_name["MouthCavity"]

        def rest_keys(deformer):
            return [0.0, 0.0, 0.0] if deformer is lips else [0.0]

        def open_keys(deformer):
            return [1.0, 0.0, 0.0] if deformer is lips else [1.0]

        def y_at_mesh(grid, rest, mesh_y):
            """把控制网格按**静止形**线性映射回网格坐标，取 ``mesh_y`` 处的实际 y。

            控制网格比网格本身大 5%，所以 t 必须用静止形的上下界算；用网格边界算
            会把 5% 余量当成"网格自己"，读数整体偏一格。
            """
            rest_lo = min(p[1] for p in rest.points)
            rest_hi = max(p[1] for p in rest.points)
            t = (mesh_y - rest_lo) / (rest_hi - rest_lo)
            lo = min(p[1] for p in grid.points)
            hi = max(p[1] for p in grid.points)
            return lo + t * (hi - lo)

        for deformer in (lips, cavity):
            rest = _grid_by_keys(deformer, rest_keys(deformer))
            opened = _grid_by_keys(deformer, open_keys(deformer))
            # 上沿钉住：张嘴只有下沿往下走
            assert y_at_mesh(opened, rest, 0.0) == pytest.approx(
                y_at_mesh(rest, rest, 0.0), abs=1e-6)
            assert y_at_mesh(opened, rest, 10.0) > y_at_mesh(rest, rest, 10.0)

        assert MOUTH_CAVITY_STRETCH > MOUTH_OPEN_STRETCH, (
            "口内必须比嘴唇张得更深，否则永远被嘴唇盖住")

        def depth(deformer):
            rest = _grid_by_keys(deformer, rest_keys(deformer))
            opened = _grid_by_keys(deformer, open_keys(deformer))
            return y_at_mesh(opened, rest, 10.0) - y_at_mesh(rest, rest, 10.0)

        assert depth(cavity) > depth(lips) * 1.5


class TestMultiAxisBandHelpers:
    def test_band_indices_run_first_axis_fastest(self):
        assert _band_indices([2, 3]) == [
            (0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)]

    def test_centered_fraction_maps_default_to_zero(self):
        breath = ParameterSpec("ParamBreath", 0.0, 1.0, 0.5)
        assert _centered_fraction(breath, 0.5) == pytest.approx(0.0)
        assert _centered_fraction(breath, 1.0) == pytest.approx(1.0)
        assert _centered_fraction(breath, 0.0) == pytest.approx(-1.0)

    def test_grid_count_must_match_axis_product(self):
        """多轴带的控制网格数必须等于各轴键数之积（内核按展平序取）。"""
        deformer = WarpDeformerSpec(
            deformer_id="Warp1", rows=2, cols=2,
            grids=[_lattice(key_value=0.0), _lattice(key_value=1.0)],
            axes=(("ParamMouthOpenY", (0.0, 1.0)),
                  ("ParamMouthForm", (-1.0, 0.0, 1.0))))
        spec = RigSpec(meshes=[_square("A")], parameters=[
            ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0),
            ParameterSpec("ParamMouthForm", -1.0, 1.0, 0.0),
        ], deformers=[deformer], pixels_per_unit=PPU)
        with pytest.raises(UnsupportedRig, match="各轴键数之积"):
            compile_static_rig(spec)


class TestSynthesisedLayerDeformersCompile:
    """闭眼 / 口内必须真的能编进 moc3（不只是 build_deformers 认可）。

    校验器会逐轴要求「键值覆盖完整区间」并且「存在恰好等于参数默认值的静止形」；
    静止形分别是 open=1（睁眼）与 open=0（闭嘴），这里把整条链路跑一遍。

    「闭眼图」（``eyelid_left/right``）是**静态**的：它画在眼球下面，靠眼球向上
    收拢"让出来"，所以它不挂任何变形器 —— 挂了反而会跟着变形跑偏。
    """

    def test_closed_eye_image_stays_static_while_the_eye_collapses(self):
        meshes = [_patch("eyelid_left", 0.0, 0.0, 40.0, 30.0),
                  _patch("eyelid_right", 60.0, 0.0, 100.0, 30.0),
                  _patch("eyewhite_left", 0.0, 0.0, 40.0, 30.0),
                  _patch("eyewhite_right", 60.0, 0.0, 100.0, 30.0),
                  _patch("mouth_cavity", 0.0, 60.0, 40.0, 66.0)]
        params = [
            ParameterSpec("ParamEyeLOpen", 0.0, 1.0, 1.0),
            ParameterSpec("ParamEyeROpen", 0.0, 1.0, 1.0),
            ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0),
        ]
        warps, skipped, _static = _build(
            meshes,
            [_entry("EyeBlink_L", ["ParamEyeLOpen"], "eye", ["eyewhite_left"],
                    cols=2),
             _entry("EyeBlink_R", ["ParamEyeROpen"], "eye", ["eyewhite_right"],
                    cols=2),
             _entry("MouthCavity", ["ParamMouthOpenY"], "mouth_cavity",
                    ["mouth_cavity"], cols=2)],
            params)

        binding = {m.mesh_id: m.deformer_id for m in meshes}
        # 闭眼图不挂变形器：它是静态的，靠眼球收拢让出来
        assert binding["eyelid_left"] == ""
        assert binding["eyelid_right"] == ""
        # 眼球挂 EyeBlink，口内挂 MouthCavity
        assert binding["eyewhite_left"] == "EyeBlink_L"
        assert binding["eyewhite_right"] == "EyeBlink_R"
        assert binding["mouth_cavity"] == "MouthCavity"
        assert skipped == []

    def test_eye_and_cavity_compile_into_a_static_rig(self):
        meshes = [_patch("eyewhite_left", 0.0, 0.0, 40.0, 30.0),
                  _patch("irides_left", 60.0, 0.0, 100.0, 30.0),
                  _patch("mouth_cavity", 0.0, 60.0, 40.0, 66.0)]
        params = [
            ParameterSpec("ParamEyeLOpen", 0.0, 1.0, 1.0),
            ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0),
        ]
        warps, skipped, _static = _build(
            meshes,
            [_entry("EyeBlink_L", ["ParamEyeLOpen"], "eye", ["eyewhite_left"],
                    cols=2),
             _entry("MouthCavity", ["ParamMouthOpenY"], "mouth_cavity",
                    ["mouth_cavity"], cols=2)],
            params)

        assert [d.deformer_id for d in warps] == [
            "EyeBlink_L", "MouthCavity"], skipped
        assert {m.mesh_id: m.deformer_id for m in meshes} == {
            "eyewhite_left": "EyeBlink_L", "irides_left": "",
            "mouth_cavity": "MouthCavity"}
        spec = RigSpec(meshes=meshes, parameters=params, deformers=warps,
                       canvas_width=120.0, canvas_height=120.0,
                       pixels_per_unit=PPU)
        doc = compile_static_rig(spec)
        assert doc is not None
        assert not lint_document(doc)


class TestOpacityKeyformLayers:
    """腮红 / 眼泪只有**不透明度**变化，靠网格键形的 opacity 驱动。

    官方 Cubism Core 5.0.0 实测：键形 opacity 0 时静止帧 0 像素、1 时 2500 像素。
    这里锁死两件事：键形数 = 各轴键数之积，且多轴不透明度取各轴归一化值的**最大**
    值（总开关或单侧任一打开都可见，见 ``OPACITY_KEYFORM_LAYERS``）。
    """

    VERTS = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]

    def _params(self, *ids):
        return {pid: ParameterSpec(pid, 0.0, 1.0, 0.0) for pid in ids}

    def test_cheek_fades_in_on_a_single_axis(self):
        spec = _opacity_keyforms("cheek", self._params("ParamCheek"), self.VERTS)

        assert spec is not None
        axes, shapes = spec
        assert axes == (("ParamCheek", (0.0, 1.0)),)
        assert len(shapes) == 2
        assert [s.opacity for s in shapes] == [0.0, 1.0]
        # 顶点不变，只有不透明度变
        assert [list(v) for v in shapes[1].vertices] == [list(v) for v in self.VERTS]

    def test_tears_open_with_either_the_master_or_one_side(self):
        spec = _opacity_keyforms(
            "tear_left",
            self._params("ParamTears", "ParamTearsL"), self.VERTS)

        assert spec is not None
        axes, shapes = spec
        assert [pid for pid, _ in axes] == ["ParamTears", "ParamTearsL"]
        assert len(shapes) == 4
        # 展平序：第一个轴步长为 1 -> (master, side) = (0,0) (1,0) (0,1) (1,1)
        # 总开关或单侧任一为 1 都要可见（乘积会失效：验收是逐参数扫描的）
        assert [s.opacity for s in shapes] == [0.0, 1.0, 1.0, 1.0]

    def test_undeclared_parameter_leaves_the_layer_static(self):
        """宁可不驱动，也不交半套：只声明了总开关时整条不挂。"""
        assert _opacity_keyforms("tear_left", self._params("ParamTears"),
                                 self.VERTS) is None

    def test_unrelated_layer_is_not_touched(self):
        assert _opacity_keyforms("irides_left", self._params("ParamCheek"),
                                 self.VERTS) is None

    def test_fade_in_layers_are_reported_as_driven(self):
        """编译端必须把它们算进「能驱动哪些参数」，否则验收端不会去验。"""
        _axes, shapes = _opacity_keyforms(
            "tear_left", self._params("ParamTears", "ParamTearsL"), self.VERTS)
        mesh = MeshSpec(
            mesh_id="tear_left", vertices=self.VERTS,
            triangles=[(0, 1, 2), (0, 2, 3)],
            uvs=[(0.0, 0.0)] * 4,
            keyform_axes=(("ParamTears", (0.0, 1.0)),
                          ("ParamTearsL", (0.0, 1.0))),
            keyform_shapes=shapes)
        spec = RigSpec(meshes=[mesh], parameters=[
            ParameterSpec("ParamTears", 0.0, 1.0, 0.0),
            ParameterSpec("ParamTearsL", 0.0, 1.0, 0.0),
        ], pixels_per_unit=PPU)

        driven = driven_parameters(spec)

        assert driven["ParamTears"] == [0.0, 1.0]
        assert driven["ParamTearsL"] == [0.0, 1.0]

    def test_opacity_keyforms_compile_into_a_static_rig(self):
        _axes, shapes = _opacity_keyforms(
            "cheek", self._params("ParamCheek"), self.VERTS)
        mesh = MeshSpec(
            mesh_id="cheek", vertices=self.VERTS,
            triangles=[(0, 1, 2), (0, 2, 3)],
            uvs=[(0.0, 0.0)] * 4,
            keyform_axes=(("ParamCheek", (0.0, 1.0)),),
            keyform_shapes=shapes)
        spec = RigSpec(meshes=[mesh],
                       parameters=[ParameterSpec("ParamCheek", 0.0, 1.0, 0.0)],
                       canvas_width=120.0, canvas_height=120.0,
                       pixels_per_unit=PPU)

        doc = compile_static_rig(spec)

        assert doc is not None
        assert not lint_document(doc)


class TestDrivenParameterReporting:
    def test_driven_parameters_covers_mesh_keyforms_and_deformer_bands(self):
        meshes = [_patch("mouth")]
        warps, _skipped, _static = _build(
            meshes,
            [_entry("MouthOpen",
                    ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
                    "mouth", ["mouth"])],
            MOUTH_PARAMS)
        mesh = MeshSpec(
            mesh_id="eye", vertices=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
            triangles=[(0, 1, 2)], uvs=[(0.0, 0.0)] * 3,
            keyform_parameter_id="ParamEyeLOpen",
            keyform_shapes=[KeyformShape(0.0, [(0.0, 0.0)] * 3),
                            KeyformShape(1.0, [(0.0, 0.0)] * 3)])
        spec = RigSpec(meshes=[mesh], parameters=MOUTH_PARAMS + [
            ParameterSpec("ParamEyeLOpen", 0.0, 1.0, 1.0),
        ], deformers=warps, pixels_per_unit=PPU)

        driven = driven_parameters(spec)

        assert set(driven) == {
            "ParamEyeLOpen", "ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"}
        assert driven["ParamMouthForm"] == [-1.0, 0.0, 1.0]

    def test_capabilities_are_written_into_the_manifest(self, tmp_path):
        manifest = tmp_path / "m.model3.json"
        manifest.write_text('{"Version": 3, "FileReferences": {}}', encoding="utf-8")
        _attach_capabilities(str(tmp_path), "m",
                             {"ParamMouthOpenY": [0.0, 1.0]})
        import json as _json
        data = _json.loads(manifest.read_text(encoding="utf-8"))
        assert data["Capabilities"]["DrivableParameters"] == ["ParamMouthOpenY"]


class TestEyeFollowsHead:
    """眼球必须随头转：眨眼与头部三轴压在同一条变形器带上。

    眼睛网格的键形带被双眼球跟踪占用，所以头部三轴只能走变形器层；一块网格又只能
    挂一个变形器，于是眨眼与转头合成多轴带（``eye_head``）。这里锁住两件事：
    转头时变形器给出的位移与头部网格键形**完全相同**，以及缺少头部信息时不会把
    眨眼一起丢掉。
    """

    _PARAMS = [
        ParameterSpec("ParamEyeLOpen", 0.0, 1.0, 1.0),
        ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0),
        ParameterSpec("ParamAngleY", -30.0, 30.0, 0.0),
        ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0),
    ]

    @classmethod
    def _build(cls, bone_positions, head_span):
        meshes = [_patch("eyewhite_left", 0.0, 0.0, 40.0, 20.0)]
        return build_deformers(
            meshes,
            [_entry("EyeBlink_L",
                    ["ParamEyeLOpen", "ParamAngleX", "ParamAngleY",
                     "ParamAngleZ"],
                    "eye_head", ["eyewhite_left"], cols=2)],
            400.0, 400.0,
            {p.parameter_id: p for p in cls._PARAMS},
            bone_positions, head_span=head_span)

    def test_the_head_rule_is_reproduced_point_by_point(self):
        warps, skipped, _static = self._build({"Head": (200.0, 60.0)},
                                              (300.0, 200.0))
        assert skipped == []
        deformer = warps[0]
        rest = _grid_by_keys(deformer, [1.0, 0.0, 0.0, 0.0])
        turned = _grid_by_keys(deformer, [1.0, 0.0, 30.0, 0.0])

        # 期望值直接用头部网格键形的那套公式算，而不是复制一份常量
        px, py = to_model_xy(200.0, 60.0, 400.0, 400.0)
        shift_x = 300.0 * HEAD_SHIFT_X
        shift_y = 200.0 * HEAD_SHIFT_Y
        depth = 300.0 * HEAD_DEPTH_SPANS
        sin_y, cos_y = math.sin(math.radians(30.0)), math.cos(math.radians(30.0))
        want = [_head_transform(x, y, px, py, shift_x, shift_y,
                                0.0, 1.0, sin_y, cos_y, 0.0, 1.0, depth, 1.0)
                for x, y in rest.points]
        _assert_points(turned.points, want)

        # 而且确实动了：单点最大位移 > 5px（弧位移 6px 被透视压回来一点）
        moved = max(math.hypot(a[0] - b[0], a[1] - b[1])
                    for a, b in zip(turned.points, rest.points))
        assert moved > 5.0

    def test_blink_still_collapses_the_eye(self):
        warps, _skipped, _static = self._build({"Head": (200.0, 60.0)},
                                               (300.0, 200.0))
        deformer = warps[0]
        opened = _grid_by_keys(deformer, [1.0, 0.0, 0.0, 0.0])
        closed = _grid_by_keys(deformer, [0.0, 0.0, 0.0, 0.0])

        def span(grid):
            ys = [p[1] for p in grid.points]
            return max(ys) - min(ys)

        assert span(closed) < span(opened) * 0.2

    def test_without_head_information_blink_survives_alone(self):
        """没有 Head 关节就退回单轴眨眼，绝不把眨眼也一起丢掉。"""
        warps, skipped, _static = self._build({}, (300.0, 200.0))
        assert skipped == []
        deformer = warps[0]
        assert [pid for pid, _keys in deformer.axes_spec] == ["ParamEyeLOpen"]
        assert len(deformer.grids) == 2


class TestExpressionLayersFollowHead:
    """腮红 / 眼泪这类**不透明度**键形层也必须随头转。

    它们的网格键形带被 ParamCheek / ParamTears 占满，头部三轴进不去；实测
    ``ParamAngleZ = 30`` 时脸转 13.15px、cheek / tear 位移 0px，腮红和泪痕
    留在原地滑出脸外。修法是一条只含头部三轴的 warp（``head`` profile）。
    """

    _PARAMS = [
        ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0),
        ParameterSpec("ParamAngleY", -30.0, 30.0, 0.0),
        ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0),
    ]

    @classmethod
    def _build(cls, bone_positions, head_span):
        meshes = [_patch("cheek", 0.0, 0.0, 40.0, 20.0)]
        return build_deformers(
            meshes,
            [_entry("CheekFollow",
                    ["ParamAngleX", "ParamAngleY", "ParamAngleZ"],
                    "head", ["cheek"], rows=3, cols=3)],
            400.0, 400.0,
            {p.parameter_id: p for p in cls._PARAMS},
            bone_positions, head_span=head_span)

    def test_cheek_follows_the_same_head_rule_as_keyforms(self):
        warps, skipped, _static = self._build({"Head": (200.0, 60.0)},
                                              (300.0, 200.0))
        assert skipped == []
        deformer = warps[0]
        assert [pid for pid, _keys in deformer.axes_spec] == [
            "ParamAngleX", "ParamAngleY", "ParamAngleZ"]
        rest = _grid_by_keys(deformer, [0.0, 0.0, 0.0])
        turned = _grid_by_keys(deformer, [0.0, 30.0, 0.0])

        px, py = to_model_xy(200.0, 60.0, 400.0, 400.0)
        sin_y, cos_y = math.sin(math.radians(30.0)), math.cos(math.radians(30.0))
        want = [_head_transform(x, y, px, py,
                                300.0 * HEAD_SHIFT_X, 200.0 * HEAD_SHIFT_Y,
                                0.0, 1.0, sin_y, cos_y, 0.0, 1.0,
                                300.0 * HEAD_DEPTH_SPANS, 1.0)
                for x, y in rest.points]
        _assert_points(turned.points, want)

    def test_without_head_information_the_layer_stays_static(self):
        """没有 Head 关节时不装会动：变形器保留（网格仍指向它）但如实记为静态。"""
        warps, skipped, _static = self._build({}, (300.0, 200.0))
        assert skipped and "头部信息不全" in skipped[0]
        deformer = warps[0]
        assert deformer.axes == ()
        assert not deformer.is_keyformed
        assert len(deformer.grids) == 1


class TestSharedBodyLean:
    """躯干倾摆要有一个**共享尺度**：同一高度上，手臂与衣服的位移必须逐点相同。

    实测（2026-11-01，singer 导出件，``ParamBodyAngleY = 10``）领子横移 26.03px、
    上身衣服 14.67px，而手臂与脖子 0px —— 衣服整件从脖子上滑走。根因是每条变形器
    各用自己成员的范围算「宽度 × 深度」，两块网格算出来的从来不是同一个变换。
    """

    _PARAMS = [
        ParameterSpec("ParamBodyAngleX", -10.0, 10.0, 0.0),
        ParameterSpec("ParamBodyAngleY", -10.0, 10.0, 0.0),
        ParameterSpec("ParamBodySway", -10.0, 10.0, 0.0),
        ParameterSpec("ParamBreath", 0.0, 1.0, 0.5),
        ParameterSpec("ParamShoulderL", -1.0, 1.0, 0.0),
    ]
    # 轴序 = profile 的角色序：torso = (angle_x, angle_y, sway, breath)，
    # lift_lean = (lift, angle_x, angle_y, sway, breath)
    _TORSO_REST = [0.0, 0.0, 0.0, 0.5]
    _TORSO_Y10 = [0.0, 10.0, 0.0, 0.5]
    _ARM_REST = [0.0, 0.0, 0.0, 0.0, 0.5]
    _ARM_Y10 = [0.0, 0.0, 10.0, 0.0, 0.5]

    def _build(self, params=None, arm_y1=10.0):
        meshes = [_patch("clothes_top", 0.0, 0.0, 40.0, 10.0),   # 躯干 x 0..40
                  _patch("arm_L", 60.0, 0.0, 80.0, arm_y1)]      # 手臂 x 60..80
        entries = [
            _entry("BodySway", ["ParamBodyAngleX", "ParamBodyAngleY",
                                "ParamBodySway", "ParamBreath"],
                   "torso", ["clothes_top"], rows=3, cols=2),
            _entry("ShoulderLift_L", ["ParamShoulderL", "ParamBodyAngleX",
                                      "ParamBodyAngleY", "ParamBodySway",
                                      "ParamBreath"],
                   "lift_lean", ["arm_L"], rows=2, cols=2),
        ]
        warps, skipped, _static = build_deformers(
            meshes, entries, 200.0, 200.0,
            {p.parameter_id: p for p in (params or self._PARAMS)}, {})
        return {w.deformer_id: w for w in warps}, skipped

    @staticmethod
    def _row_delta(deformer, rest_values, posed_values, want_top):
        """静止形与倾摆形逐点相减，取最上（肩）或最下（胯）那一行的位移。

        同一行所有控制点的位移必须相同 —— 否则说明形变里混进了横向梯度。
        """
        rest = _grid_by_keys(deformer, rest_values)
        posed = _grid_by_keys(deformer, posed_values)
        rows = {}
        for (rx, ry), (px, py) in zip(rest.points, posed.points):
            rows.setdefault(round(ry, 4), []).append((px - rx, py - ry))
        key = min(rows) if want_top else max(rows)
        deltas = rows[key]
        for dx, dy in deltas:
            assert abs(dx - deltas[0][0]) < 1e-9 and abs(dy - deltas[0][1]) < 1e-9
        return deltas[0]

    def test_arm_and_torso_lean_by_the_same_amount_at_the_same_height(self):
        warps, skipped = self._build()
        assert skipped == []
        torso, arm = warps["BodySway"], warps["ShoulderLift_L"]
        # 共享宽度 = 两块成员并集 0..80，不是各自的 40 / 20
        want = 80.0 * TORSO_LEAN_X

        assert self._row_delta(torso, self._TORSO_REST, self._TORSO_Y10,
                               want_top=True)[0] == pytest.approx(want)
        assert self._row_delta(arm, self._ARM_REST, self._ARM_Y10,
                               want_top=True)[0] == pytest.approx(want)
        # 胯部钉住：两条变形器在底行都不动
        assert self._row_delta(torso, self._TORSO_REST, self._TORSO_Y10,
                               want_top=False)[0] == pytest.approx(0.0)
        assert self._row_delta(arm, self._ARM_REST, self._ARM_Y10,
                               want_top=False)[0] == pytest.approx(0.0)

    def test_shoulder_lift_amplitude_is_still_the_arm_s_own_height(self):
        """叠加了倾摆轴，抬肩幅度不该跟着变（按成员静止高度 10 算）。"""
        warps, _skipped = self._build()
        arm = warps["ShoulderLift_L"]
        dx, dy = self._row_delta(arm, self._ARM_REST,
                                 [1.0, 0.0, 0.0, 0.0, 0.5], want_top=True)
        assert dx == pytest.approx(0.0)
        assert dy == pytest.approx(-SHOULDER_LIFT * 10.0)

    def test_missing_body_axis_falls_back_to_plain_lift(self):
        """身体轴参数不全时退回「只抬肩」，不能把抬肩一起变成静态。"""
        params = [p for p in self._PARAMS if p.parameter_id != "ParamBodySway"]
        warps, _skipped = self._build(params)
        arm = warps["ShoulderLift_L"]
        assert [pid for pid, _keys in arm.axes_spec] == ["ParamShoulderL"]
        assert len(arm.grids) == 3
        dx, dy = self._row_delta(arm, [0.0], [1.0], want_top=True)
        assert dx == pytest.approx(0.0)
        assert dy == pytest.approx(-SHOULDER_LIFT * 10.0)

    def test_lean_is_pinned_at_the_waist_not_at_the_lowest_member(self):
        """锚点必须是**躯干本体**的下缘，不是并集下缘。

        分层器切出来的衣服是零重叠的硬接（singer：``clothes_top`` 202..476 /
        ``waistband`` 477..505 / ``clothes_bottom`` 506..833）。手臂垂到腰以下会把并集
        底拖到大腿，于是腰带下缘带着位移离开裙摆 —— ``ParamBreath = 1`` 时整条腰
        裂开一道贯通背景的缝。锚点钉在腰线以后，腰带与裙摆在这一行都不动。
        """
        # 手臂垂到 y=30（躯干只到 y=10）
        warps, _skipped = self._build(arm_y1=30.0)
        torso, arm = warps["BodySway"], warps["ShoulderLift_L"]
        # 躯干最下那一行（腰线附近）位移为 0：腰带不会从裙摆上滑开
        assert self._row_delta(torso, self._TORSO_REST, self._TORSO_Y10,
                               want_top=False)[0] == pytest.approx(0.0)
        # 手臂垂到腰线以下的部分同样为 0
        assert self._row_delta(arm, self._ARM_REST, self._ARM_Y10,
                               want_top=False)[0] == pytest.approx(0.0)
        # 而肩部仍然按同一个共享尺度一起摆（宽度 = 并集 0..80）
        assert self._row_delta(torso, self._TORSO_REST, self._TORSO_Y10,
                               want_top=True)[0] == pytest.approx(
            80.0 * TORSO_LEAN_X, abs=1e-6)


# ----------------------------------------------------------------------
# Task 1.5：根级（无父变形器）的产物字节钉死
# ----------------------------------------------------------------------

def _rect_lattice(rows, cols, x0, y0, x1, y1, key_value=0.0, dx=0.0, dy=0.0):
    """(rows+1)x(cols+1) 的轴对齐规则点阵（`_lattice` 只会画正方的那几种）。

    ``dx`` 是**两端的水平弯**（中间列不动，越靠边越弯），``dy`` 是两端的垂直弯：
    键形之间因此不是纯平移，能钉住「写错轴 / 写反符号」。dx=dy=0 的静止形仍是
    规则点阵（`_require_axis_lattice` 只查静止形）。
    """
    step_x = (x1 - x0) / cols
    step_y = (y1 - y0) / rows
    points = []
    for r in range(rows + 1):
        # 行方向的比例：两端 ±1、中间 0（奇数行时正中一行完全不弯）
        fy = (2.0 * r / rows) - 1.0
        for c in range(cols + 1):
            fx = (2.0 * c / cols) - 1.0
            points.append((x0 + c * step_x + dx * fx, y0 + r * step_y + dy * fy))
    return DeformerGrid(key_value, points)


def _flat_mesh(mesh_id, verts, deformer_id="", part_id="",
               keyform_axis=None, opacity=1.0, draw_order=0.0):
    """四边形网格；``keyform_axis`` = (参数 id, [(键值, 顶点表), ...])。"""
    shapes = ()
    pid = ""
    if keyform_axis:
        pid, keyed = keyform_axis
        shapes = [KeyformShape(float(k), list(v)) for k, v in keyed]
    return MeshSpec(mesh_id=mesh_id, vertices=list(verts),
                    triangles=[(0, 1, 2), (0, 2, 3)],
                    uvs=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                    draw_order=draw_order, opacity=opacity, part_id=part_id,
                    deformer_id=deformer_id, keyform_parameter_id=pid,
                    keyform_shapes=shapes)


def _spec_with_flat_warps() -> RigSpec:
    """全部变形器都是**根级**（``parent_deformer_id`` 为空）的规格 —— 今天真实产物的形状。

    刻意覆盖已验收模型走到的每一条写出路径，好让黄金字节真的能代表它们：
      * 静态根 warp（单控制网格）+ 挂在它下面的网格（顶点写成 (s, t)）；
      * 单轴三键根 warp（表情类）；
      * 双轴多键根 warp（2x3 矩形点阵，非正方形 rows/cols）；
      * 静态 rotation 与带键形的 rotation（顶点仍是模型像素）；
      * 同时带自身键形又挂在 warp 下的网格；
      * 不挂任何变形器的网格。
    画布与 PPU 都取**非默认**且各轴不同（512x768 / 64），这样「多除一次 ppu」
    「x/y 写反」这类错误也会改字节。
    """
    params = [
        ParameterSpec("ParamMood", -1.0, 1.0, 0.0),
        ParameterSpec("ParamMouthOpenY", 0.0, 1.0, 0.0),
        ParameterSpec("ParamMouthForm", -1.0, 1.0, 0.0),
        ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0),
    ]
    warp_static = WarpDeformerSpec(
        deformer_id="WarpStatic", rows=2, cols=2,
        grids=[_rect_lattice(2, 2, -50.0, -70.0, 50.0, 70.0)],
        parent_part_id="Part_Head")
    warp_single = WarpDeformerSpec(
        deformer_id="WarpMood", rows=1, cols=3,
        grids=[_rect_lattice(1, 3, 10.0, -20.0, 130.0, 20.0,
                             key_value=-1.0, dx=-4.0, dy=-2.0),
               _rect_lattice(1, 3, 10.0, -20.0, 130.0, 20.0),
               _rect_lattice(1, 3, 10.0, -20.0, 130.0, 20.0,
                             key_value=1.0, dx=4.0, dy=2.0)],
        parameter_id="ParamMood", parent_part_id="Part_Mouth")
    # 双轴带：展平序 index = i_open + 2*i_form（**第一个轴步长为 1**，F-06 实测定论）
    two_axis_grids = []
    for form in (-1.0, 0.0, 1.0):
        for open_y in (0.0, 1.0):
            two_axis_grids.append(_rect_lattice(
                2, 3, -30.0, -12.0, 66.0, 44.0, key_value=open_y,
                dx=9.0 * open_y, dy=6.0 * form))
    warp_two_axis = WarpDeformerSpec(
        deformer_id="WarpMouth", rows=2, cols=3, grids=two_axis_grids,
        axes=(("ParamMouthOpenY", (0.0, 1.0)),
              ("ParamMouthForm", (-1.0, 0.0, 1.0))),
        parent_part_id="Part_Lips")
    rotation_static = RotationDeformerSpec(
        deformer_id="RotStatic", parent_part_id="Part_Arm",
        keyforms=[RotationKeyform(0.0, angle=12.5, origin=(18.0, -22.0), scale=1.05)])
    rotation_keyformed = RotationDeformerSpec(
        deformer_id="RotHead", parameter_id="ParamAngleZ",
        parent_part_id="Part_Neck",
        keyforms=[RotationKeyform(-30.0, angle=-24.0, origin=(0.0, 40.0), scale=1.1),
                  RotationKeyform(0.0, angle=0.0, origin=(0.0, 40.0), scale=1.0),
                  RotationKeyform(30.0, angle=24.0, origin=(0.0, 40.0), scale=0.9)])

    meshes = [
        _flat_mesh("ArtHead", [(-40.0, -60.0), (40.0, -60.0), (40.0, 60.0),
                               (-40.0, 60.0)],
                   deformer_id="WarpStatic", part_id="Part_Head"),
        _flat_mesh("ArtMouth", [(-12.0, -18.0), (18.0, -18.0), (18.0, 18.0),
                                (-12.0, 18.0)],
                   deformer_id="WarpMood", part_id="Part_Mouth"),
        _flat_mesh("ArtLips", [(-24.0, -9.0), (24.0, -9.0), (24.0, 9.0),
                               (-24.0, 9.0)],
                   deformer_id="WarpMouth", part_id="Part_Lips"),
        _flat_mesh("ArtArm", [(60.0, -140.0), (120.0, -140.0), (120.0, -80.0),
                              (60.0, -80.0)],
                   deformer_id="RotStatic", part_id="Part_Arm"),
        _flat_mesh("ArtNeck", [(-8.0, 80.0), (8.0, 80.0), (8.0, 110.0),
                               (-8.0, 110.0)],
                   deformer_id="RotHead", part_id="Part_Neck"),
        _flat_mesh("ArtFree", [(-200.0, -200.0), (-150.0, -200.0),
                               (-150.0, -150.0), (-200.0, -150.0)],
                   part_id="Part_Free",
                   keyform_axis=("ParamMood",
                                 [(-1.0, [(-200.0, -200.0), (-150.0, -200.0),
                                          (-152.0, -148.0), (-202.0, -148.0)]),
                                  (0.0, [(-200.0, -200.0), (-150.0, -200.0),
                                         (-150.0, -150.0), (-200.0, -150.0)]),
                                  (1.0, [(-200.0, -200.0), (-150.0, -200.0),
                                         (-148.0, -152.0), (-198.0, -152.0)])])),
        _flat_mesh("ArtKeyedOnWarp", [(-20.0, -30.0), (20.0, -30.0),
                                      (20.0, 30.0), (-20.0, 30.0)],
                   deformer_id="WarpStatic", part_id="Part_Head",
                   keyform_axis=("ParamMouthOpenY",
                                 [(0.0, [(-20.0, -30.0), (20.0, -30.0),
                                         (20.0, 30.0), (-20.0, 30.0)]),
                                  (1.0, [(-20.0, -30.0), (20.0, -30.0),
                                         (26.0, 34.0), (-26.0, 34.0)])])),
    ]
    return RigSpec(meshes=meshes, parameters=params,
                   deformers=[warp_static, warp_single, warp_two_axis,
                              rotation_static, rotation_keyformed],
                   canvas_width=512.0, canvas_height=768.0,
                   pixels_per_unit=64.0)


def test_flat_warp_fixture_is_all_root_level():
    """黄金字节要钉的是「只有根级」的产物 —— 夹具里不许出现父变形器。

    夹具哪天被加了 parent，这条就红：那份对照字节就不再是根级基准了，
    必须用未改动的编译器重新生成一次，不能顺手把断言删掉。
    """
    spec = _spec_with_flat_warps()
    assert [d.parent_deformer_id for d in spec.deformers] == [""] * len(spec.deformers)
    spec.validate()
    assert lint_document(compile_static_rig(spec)) == []


def test_root_level_warp_bytes_are_unchanged_by_the_space_map():
    """根级 warp（parent_deformer_id 为空）的产物字节必须一字不差。

    实测：不挂变形器与挂一级恒等 warp 的两帧逐像素相同（渲染 SHA ``e035d547d377…``，
    官方 Core 侧的同一约定由 tests/unit/test_deformer_parent_chain.py 的
    ``test_attaching_an_identity_chain_leaves_the_rest_frame_alone`` 守住，离线侧由
    tests/unit/test_bindings_gap_probe.py 的 ``_rule_i_from_moc3`` 复算核对）——
    今天产物里 13 个根变形器全都依赖这条。
    改空间基准时如果动了根级，所有已验收过的模型都会静默变形。

    黄金字节由**改动前**的编译器（Task 1.5 的实现提交之前）生成，落成 base64 文本
    夹具 ``tests/unit/fixtures/moc3_root_level_flat_warps_golden.b64``
    （仓库 ``.gitignore`` 的 ``*.bin`` 是权重文件规则，二进制夹具进不了库）：
    ``base64.b64encode(compile_static_rig(_spec_with_flat_warps()).to_bytes())``
    当时产物 10880 字节，sha256 = ``66ab863352dba657295f…eca5e8c8``。
    """
    spec = _spec_with_flat_warps()
    assert _GOLDEN_FLAT_WARP_BYTES_FILE.is_file(), (
        f"缺黄金字节文件：{_GOLDEN_FLAT_WARP_BYTES_FILE}"
        "（必须用未改动的编译器生成一次，见本用例 docstring）")
    assert len(_golden_flat_warp_bytes()) == 10880, (
        "黄金字节夹具长度变了（应为 10880，被改动前的编译器产出）")
    assert compile_static_rig(spec).to_bytes() == _golden_flat_warp_bytes(), (
        "根级写出路径变了：Task 1.5 只允许改父级非空的那条分支")


# ----------------------------------------------------------------------
# Task 1.5 Step 3：嵌套 warp 的空间基准（规则 I）
# ----------------------------------------------------------------------

def _unit_grid(doc, slot):
    """读回写进 moc3 的第 ``slot`` 张 warp 点阵（单位坐标）。"""
    begins = doc.get("warp_deformer_keyform.keyform_position_begin_indices")
    xs = doc.get("keyform_position.xys")
    begin = begins[slot]
    points = doc.get("warp_deformer.vertex_counts")[slot]
    return _pool_positions(doc, begin, points)


def _mesh_st(doc, slot=0):
    """读回挂在 warp 下的网格顶点（无量纲 (s, t)）。"""
    begins = doc.get("art_mesh_keyform.keyform_position_begin_indices")
    counts = doc.get("art_mesh.position_index_counts")   # = 唯一顶点数（官方反直觉命名）
    return _pool_positions(doc, begins[slot], counts[slot])


def _nested_spec(parent_rect=(-100.0, -100.0, 100.0, 100.0),
                 child_rect=(-100.0, -100.0, 100.0, 100.0),
                 grand_rect=None, mesh_rect=(-60.0, -60.0, 60.0, 60.0)):
    """根 warp + 子 warp（+ 可选孙 warp），方块挂在最深一级；坐标都是模型像素。"""
    def corners(rect):
        x0, y0, x1, y1 = rect
        return [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]

    root = WarpDeformerSpec("L0", 1, 1, [DeformerGrid(0.0, corners(parent_rect))])
    child = WarpDeformerSpec("L1", 1, 1, [DeformerGrid(0.0, corners(child_rect))],
                             parent_deformer_id="L0")
    deformers = [root, child]
    deepest = "L1"
    if grand_rect is not None:
        deformers.append(WarpDeformerSpec(
            "L2", 1, 1, [DeformerGrid(0.0, corners(grand_rect))],
            parent_deformer_id="L1"))
        deepest = "L2"
    mesh = MeshSpec("ArtProbe", corners(mesh_rect), [(0, 1, 2), (0, 2, 3)],
                    [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                    deformer_id=deepest)
    return RigSpec(meshes=[mesh], deformers=deformers,
                   parameters=[ParameterSpec("ParamUnused", -1.0, 1.0, 0.0)],
                   pixels_per_unit=PPU)


def test_root_level_space_map_is_the_identity():
    """``S_root`` 必须是恒等：根级的单位就是模型像素、以 0 为中心。

    实测 R0（不挂变形器）与 R1（一级恒等 warp）逐像素相同，官方实样里那唯一一个
    根 warp 的静止矩形也以 0 为中心（x∈[−0.8795, 0.8795]）。给根级套上映射就等于
    把已验收的每个模型都改一遍。
    """
    space_map = deformer_space_map(_nested_spec())
    for point in ((-100.0, 37.5), (0.0, 0.0), (60.0, -60.0)):
        assert space_map["L0"](point) == pytest.approx(point)
    # 写出层只对父级非空的分支调用映射，根级产物由上面的黄金字节钉住


def test_nested_warp_lattice_is_written_in_the_parent_fraction_space():
    """子级的点阵写进**父级的 (s,t) 空间**，不再存模型像素。

    实测：父级 ±1 单位（±100px）时，子级若仍存模型像素，内核按 ``out = −1 + 2p``
    直接把它当父级 s 用 ⇒ 静止帧线性 2× 且中心移到父矩形最小角（R2 的
    17578/4371 = 4.02 面积比）。诊断 R3 把子级网格改存父级 st 后逐像素等于对照。
    """
    doc = compile_static_rig(_nested_spec())
    _assert_points(_unit_grid(doc, 0),
                   [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)])
    # 子级 ±100px 正好落在父级 st 的 0..1 上（×PPU 只是像素口径，写出时再 ÷PPU）
    _assert_points(_unit_grid(doc, 1),
                   [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])
    # 成员顶点按**自身**静止形归一，与诊断 R3 写出的 (s,t) 逐条相同
    _assert_points(_mesh_st(doc),
                   [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)])


def test_nested_lattice_follows_the_parent_rect_not_its_own_size():
    """父矩形 ±150、子级覆盖 ±100 时，子级点阵 = 子区域在父 st 里的位置。

    这条排掉「pixels_per_unit 被乘两次」与「父级行列差一格」：两者都会让比例随
    父矩形大小改变，诊断 R5 因此必须逐像素等于对照。
    """
    doc = compile_static_rig(_nested_spec(
        parent_rect=(-150.0, -150.0, 150.0, 150.0)))
    _assert_points(_unit_grid(doc, 0),
                   [(-1.5, -1.5), (1.5, -1.5), (-1.5, 1.5), (1.5, 1.5)])
    _assert_points(_unit_grid(doc, 1),
                   [(1 / 6, 1 / 6), (5 / 6, 1 / 6), (1 / 6, 5 / 6), (5 / 6, 5 / 6)],
                   rel=1e-9)
    _assert_points(_mesh_st(doc),
                   [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)])


def test_third_level_is_normalized_by_the_level_directly_above_it():
    """三级链逐级都用**上一级写出去那张网格**的矩形归一，不是模型像素矩形。

    拿诊断 R4（三级恒等，实测等于对照）核对：L1 落在 L0 的 st 里是 [0,1]，
    L2 落在 L1 的存储空间里也是 [0,1]。规则 N（每级按自身静止矩形重归一）在
    父矩形与子矩形不同尺寸时会给出一套完全不同的数值 —— 上面那条就是它。
    """
    doc = compile_static_rig(_nested_spec(grand_rect=(-100.0, -100.0,
                                                      100.0, 100.0)))
    for slot in (1, 2):
        _assert_points(_unit_grid(doc, slot),
                       [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])
    _assert_points(_mesh_st(doc),
                   [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)])
    # 父矩形更宽时第三级跟着走：L1 = [1/6,5/6]、L2 = [0,1]（按 L1 写出的矩形归一）
    wide = compile_static_rig(_nested_spec(
        parent_rect=(-150.0, -150.0, 150.0, 150.0),
        child_rect=(-100.0, -100.0, 100.0, 100.0),
        grand_rect=(-100.0, -100.0, 100.0, 100.0)))
    _assert_points(_unit_grid(wide, 1),
                   [(1 / 6, 1 / 6), (5 / 6, 1 / 6), (1 / 6, 5 / 6), (5 / 6, 5 / 6)],
                   rel=1e-9)
    _assert_points(_unit_grid(wide, 2),
                   [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], rel=1e-9)


def test_space_map_uses_the_parents_rest_rect_for_every_keyform():
    """父级的**静止**矩形定义子级空间；父级自己的形变键形在同一空间里另有点位。

    主干要的就是这条：父级离开静止形时子级的 st 不变，于是被父级点阵一起带走
    （渲染证据见 tests/unit/test_deformer_parent_chain.py 的 Step 5 用例）。
    """
    parent = WarpDeformerSpec(
        "L0", 1, 1,
        [DeformerGrid(-30.0, [(-140.0, -100.0), (60.0, -100.0),
                              (-140.0, 100.0), (60.0, 100.0)]),
         DeformerGrid(0.0, [(-100.0, -100.0), (100.0, -100.0),
                            (-100.0, 100.0), (100.0, 100.0)]),
         DeformerGrid(30.0, [(-60.0, -100.0), (140.0, -100.0),
                             (-60.0, 100.0), (140.0, 100.0)])],
        parameter_id="ParamAngleZ")
    child = WarpDeformerSpec(
        "L1", 1, 1, [DeformerGrid(0.0, [(-100.0, -100.0), (100.0, -100.0),
                                        (-100.0, 100.0), (100.0, 100.0)])],
        parent_deformer_id="L0")
    mesh = MeshSpec("ArtProbe", [(-60.0, -60.0), (60.0, -60.0), (-60.0, 60.0),
                                 (60.0, 60.0)], [(0, 1, 2), (0, 2, 3)],
                    [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                    deformer_id="L1")
    spec = RigSpec(meshes=[mesh], deformers=[parent, child],
                   parameters=[ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0)],
                   pixels_per_unit=PPU)
    doc = compile_static_rig(spec)
    begins = doc.get("warp_deformer_keyform.keyform_position_begin_indices")
    # 父级三个键形仍是模型单位（静止 ±1.0，满幅 ±1.4）；根级一条没被映射碰过
    _assert_points(_pool_positions(doc, begins[0], 4)[:2],
                   [(-1.4, -1.0), (0.6, -1.0)])
    _assert_points(_pool_positions(doc, begins[1], 4)[:2],
                   [(-1.0, -1.0), (1.0, -1.0)])
    _assert_points(_pool_positions(doc, begins[2], 4)[:2],
                   [(-0.6, -1.0), (1.4, -1.0)])
    # 子级那一格落在父级 st 的 [0,1]：静止矩形取父级**默认键**的矩形
    _assert_points(_pool_positions(doc, begins[3], 4),
                   [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])
    _assert_points(_mesh_st(doc),
                   [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)])


def test_warp_under_rotation_is_refused():
    """跨 rotation 的顶点空间没实测过（官方有 59 条这类边），必须拒绝而不是猜。

    主干全用 warp，不碰这条；报错文案要点名「未实测」，否则后人会当成格式 bug
    顺手改成「照 warp 的规矩办」。
    """
    rotation = RotationDeformerSpec(
        deformer_id="Rot", keyforms=[RotationKeyform(0.0, angle=10.0)])
    warp = WarpDeformerSpec("W", 1, 1, [DeformerGrid(
        0.0, [(-100.0, -100.0), (100.0, -100.0), (-100.0, 100.0), (100.0, 100.0)])],
        parent_deformer_id="Rot")
    mesh = MeshSpec("Art", [(-60.0, -60.0), (60.0, -60.0), (-60.0, 60.0),
                            (60.0, 60.0)], [(0, 1, 2), (0, 2, 3)],
                    [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                    deformer_id="W")
    spec = RigSpec(meshes=[mesh], deformers=[rotation, warp],
                   pixels_per_unit=PPU)
    with pytest.raises(UnsupportedRig, match="rotation"):
        compile_static_rig(spec)


def test_warp_parent_cycle_is_refused():
    """父链成环时空间基准没有起点，必须报错而不是无限递归。"""
    a = WarpDeformerSpec("A", 1, 1, [DeformerGrid(
        0.0, [(-100.0, -100.0), (100.0, -100.0), (-100.0, 100.0), (100.0, 100.0)])],
        parent_deformer_id="B")
    b = WarpDeformerSpec("B", 1, 1, [DeformerGrid(
        0.0, [(-100.0, -100.0), (100.0, -100.0), (-100.0, 100.0), (100.0, 100.0)])],
        parent_deformer_id="A")
    mesh = MeshSpec("Art", [(-60.0, -60.0), (60.0, -60.0), (-60.0, 60.0),
                            (60.0, 60.0)], [(0, 1, 2), (0, 2, 3)],
                    [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                    deformer_id="A")
    spec = RigSpec(meshes=[mesh], deformers=[a, b], pixels_per_unit=PPU)
    with pytest.raises(UnsupportedRig, match="成环"):
        compile_static_rig(spec)


# ----------------------------------------------------------------------
# Task 2：作者层的 ``under`` 接成真正的 parent_deformer_id
#
# ``DEFAULT_WARP_DEFORMERS`` 每条都写了 ``"parent": "Head"`` 这类**骨骼名**
# （bones/deformers.py），而变形器之间的父链在 moc3 里是另一个东西
# （``WarpDeformerSpec.parent_deformer_id``）。本任务新增一个不与骨骼 parent
# 冲突的键 ``under``（值 = 变形器名），把它解析成父链，并在父级没落地时如实降级。
# ----------------------------------------------------------------------

def test_resolve_deformer_parents_keeps_only_declared_legal_edges():
    from live2d_builder.exporter.moc3_pipeline import resolve_deformer_parents
    entries = [
        {"name": "BodySway", "type": "warp"},
        {"name": "HeadRig", "type": "warp", "under": "BodySway"},
        {"name": "MouthOpen", "type": "warp", "under": "HeadRig"},
        {"name": "Orphan", "type": "warp", "under": "NoSuchDeformer"},
        {"name": "Self", "type": "warp", "under": "Self"},
        {"name": "CycleA", "type": "warp", "under": "CycleB"},
        {"name": "CycleB", "type": "warp", "under": "CycleA"},
    ]
    assert resolve_deformer_parents(entries) == {
        "HeadRig": "BodySway", "MouthOpen": "HeadRig",
    }


def test_resolve_deformer_parents_uses_under_not_the_bone_parent():
    """``parent`` 已经是骨骼名，不许被当成变形器父级。

    最难区分的形状：某个变形器的**名字**恰好等于另一条想挂上去的骨骼名。
    只认 ``under`` 才让「骨骼归属」和「变形链」两层各说各的。
    """
    from live2d_builder.exporter.moc3_pipeline import resolve_deformer_parents
    entries = [
        {"name": "HeadRig", "type": "warp", "parent": "Head"},
        {"name": "MouthOpen", "type": "warp", "parent": "HeadRig"},
        {"name": "CheekFollow", "type": "warp", "parent": "HeadRig",
         "under": "HeadRig"},
    ]
    assert resolve_deformer_parents(entries) == {"CheekFollow": "HeadRig"}


def test_build_deformers_writes_parent_deformer_id():
    meshes = [_patch("face"), _patch("mouth")]
    entries = [
        _entry("HeadRig", [], "head", ["face"]),
        _entry("MouthOpen", [], "mouth", ["mouth"], under="HeadRig"),
    ]
    deformers, _notes, _static = _build(meshes, entries, [])
    by_id = {d.deformer_id: d for d in deformers}
    assert by_id["HeadRig"].parent_deformer_id == ""
    assert by_id["MouthOpen"].parent_deformer_id == "HeadRig"


def test_build_deformers_writes_parent_deformer_id_for_rotation_entries():
    """rotation 条目的 ``under`` 同样落地：公共表 ``deformer.parent_deformer_indices``
    覆盖两张子表，只接 warp 那一半就等于留一条写不出父链的路。

    反方向（warp 挂在 rotation 下）不在这里放开：跨 rotation 的顶点空间没实测过，
    编译器照旧拒绝（``test_warp_under_rotation_is_refused``）。
    """
    meshes = [_patch("face"), _patch("arm_L")]
    entries = [
        _entry("BodySway", [], "torso", ["face"]),
        {"name": "ArmSwing", "type": "rotation", "parent": "Arm_L",
         "pivot": [10.0, 5.0], "targets": ["arm_L"], "under": "BodySway"},
    ]
    deformers, _notes, _static = _build(meshes, entries, [])
    by_id = {d.deformer_id: d for d in deformers}
    assert by_id["ArmSwing"].parent_deformer_id == "BodySway"


def test_build_deformers_downgrades_when_parent_not_emitted():
    """父级 entry 因为「没有成员网格」被跳过时，子级必须退回无父级并留痕。"""
    meshes = [_patch("mouth")]
    entries = [
        _entry("HeadRig", [], "head", []),
        _entry("MouthOpen", [], "mouth", ["mouth"], under="HeadRig"),
    ]
    deformers, notes, _static = _build(meshes, entries, [])
    assert deformers[0].parent_deformer_id == ""
    assert any("HeadRig" in n and "未落地" in n for n in notes), notes


def test_build_deformers_keeps_parents_of_unrelated_entries_alone():
    """没有写 ``under`` 的条目一律是根级 —— 今天 13/13 的形状。"""
    meshes = [_patch("face"), _patch("mouth")]
    entries = [
        _entry("HeadRig", [], "head", ["face"]),
        _entry("MouthOpen", [], "mouth", ["mouth"]),
    ]
    deformers, notes, _static = _build(meshes, entries, [])
    assert [d.parent_deformer_id for d in deformers] == ["", ""]
    assert not any("未落地" in n for n in notes), notes


# ---- 作者键的透传：create_warp_deformer 用固定键集重建字典，under 会被静默丢掉 ----

def test_create_warp_deformer_threads_the_authored_under_key():
    from live2d_builder.bones.deformers import DeformerHierarchy
    hierarchy = DeformerHierarchy()
    d = hierarchy.create_warp_deformer(name="MouthOpen", parent="Mouth",
                                       targets=["mouth"], under="HeadRig")
    assert d["under"] == "HeadRig"
    # 没写 under 就不许冒出空键：条目字典的形状就是今天产物的输入形状
    plain = hierarchy.create_warp_deformer(name="MouthOpen", parent="Mouth",
                                           targets=["mouth"])
    assert "under" not in plain


def test_deformer_hierarchy_build_does_not_drop_the_authored_under(monkeypatch):
    """``build()`` 用固定 kwargs 调 create_warp_deformer —— 两处都得接住。

    只在 ``create_warp_deformer`` 加形参不够：``build()`` 那边不传就永远是空；
    只改 ``build()`` 也不够：字典在 create 里被重建，键会掉。这条同时压两端。
    """
    from live2d_builder.bones.deformers import DeformerHierarchy
    authored = [
        {"name": "HeadRig", "parent": "Head", "rows": 2, "cols": 2,
         "type": "warp", "targets": ["face"]},
        {"name": "MouthOpen", "parent": "Mouth", "rows": 2, "cols": 2,
         "type": "warp", "targets": ["mouth"], "under": "HeadRig"},
    ]
    monkeypatch.setattr(DeformerHierarchy, "DEFAULT_WARP_DEFORMERS", authored)
    names = ["face", "mouth"]
    built = DeformerHierarchy().build(
        {}, {n: {"vertices": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]}
             for n in names}, layer_names=names)
    by_name = {d["name"]: d for d in built["deformers"]}
    assert by_name["MouthOpen"].get("under") == "HeadRig"
    assert "under" not in by_name["HeadRig"]
    # 骨骼 parent 一个字节都不许动
    assert by_name["MouthOpen"]["parent"] == "Mouth"


def test_under_survives_from_the_authored_list_to_parent_deformer_id(monkeypatch):
    """`under` 的通路总线：DEFAULT_WARP_DEFORMERS → create_warp_deformer →
    build() 的条目 → build_deformers → ``WarpDeformerSpec.parent_deformer_id``。

    中间任何一环用固定键集重建字典，这个键都会**静默**丢掉 —— 产物看上去只是
    「没接父级」，不报错也不留痕，正是本任务最容易做错的样子。
    """
    from live2d_builder.bones.deformers import DeformerHierarchy
    authored = [
        {"name": "HeadRig", "parent": "Head", "rows": 2, "cols": 2,
         "type": "warp", "targets": ["face"]},
        {"name": "MouthOpen", "parent": "Mouth", "rows": 2, "cols": 2,
         "type": "warp", "targets": ["mouth"], "under": "HeadRig"},
    ]
    monkeypatch.setattr(DeformerHierarchy, "DEFAULT_WARP_DEFORMERS", authored)
    names = ["face", "mouth"]
    built = DeformerHierarchy().build(
        {}, {n: {"vertices": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]}
             for n in names}, layer_names=names)
    meshes = [_patch("face"), _patch("mouth")]
    deformers, _notes, _static = _build(meshes, built["deformers"], [])
    by_id = {d.deformer_id: d for d in deformers}
    assert by_id["MouthOpen"].parent_deformer_id == "HeadRig"


# ---- Task 1.5 审查前移项 1：变形器表的顺序（父级必须先于子级写出） ----

_L0_LATTICE = [(-100.0, -100.0), (100.0, -100.0),
               (-100.0, 100.0), (100.0, 100.0)]
_PROBE_SQUARE = [(-60.0, -60.0), (60.0, -60.0), (-60.0, 60.0), (60.0, 60.0)]


def _nested_pair_spec(child_first: bool) -> RigSpec:
    """根 warp L0 ← 子 warp L1，方块挂在 L1 下；``child_first`` 即「写反的表」。

    ``under`` 一旦接通，表顺序就由 ``DEFAULT_WARP_DEFORMERS`` 的书写顺序决定，
    而官方 Core 只被实测过「父级在前」那一种（Task 1 / Task 1.5 的全部渲染读数）。
    """
    root = WarpDeformerSpec("L0", 1, 1, [DeformerGrid(0.0, _L0_LATTICE)])
    child = WarpDeformerSpec("L1", 1, 1, [DeformerGrid(0.0, _L0_LATTICE)],
                             parent_deformer_id="L0")
    mesh = MeshSpec("ArtProbe", _PROBE_SQUARE, [(0, 1, 2), (0, 2, 3)],
                    [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                    deformer_id="L1")
    return RigSpec(meshes=[mesh],
                   deformers=[child, root] if child_first else [root, child],
                   pixels_per_unit=PPU)


def test_child_before_parent_table_is_written_parent_first():
    """子级排在父级前面的表必须在写出前被规范化，否则槽位引用跟着作者手感走。"""
    doc = compile_static_rig(_nested_pair_spec(child_first=True))
    assert doc.get("deformer.ids") == ["L0", "L1"]
    assert doc.get("deformer.parent_deformer_indices") == [-1, 0]
    assert doc.get("art_mesh.parent_deformer_indices") == [1]


def test_reordered_deformer_table_compiles_to_the_same_bytes():
    """规范化后的产物与作者本来就写对的表**逐字节相同** —— 表序不再是自由量。

    注意这条证的是**规范化本身**（子先父后被搬成父先子后，结果与写对的表同），
    **不是**「表序与产物字节无关」：审查员独立查过，重排确实会改字节（band 去重
    顺序、位置池顺序都跟着表序走）。真正的前提是 **parent-first 是唯一被官方 Core
    量过的顺序**，所以搬动它是把产物搬回被量过的那一种，而不是免费的整理。
    """
    assert (compile_static_rig(_nested_pair_spec(child_first=True)).to_bytes()
            == compile_static_rig(_nested_pair_spec(child_first=False)).to_bytes())


def test_parent_first_ordering_is_a_no_op_without_parents():
    """没有父级（今天 13/13 的产物形状）时不许改动条目对象与顺序。

    这条是「插管不改画面」的结构保证：黄金字节夹具
    ``test_root_level_warp_bytes_are_unchanged_by_the_space_map`` 从产物侧再钉一次。
    """
    from live2d_builder.exporter.moc3_model import parent_first_deformer_order
    deformers = [WarpDeformerSpec("B", 1, 1, [DeformerGrid(0.0, _L0_LATTICE)]),
                 WarpDeformerSpec("A", 1, 1, [DeformerGrid(0.0, _L0_LATTICE)])]
    ordered = parent_first_deformer_order(deformers)
    assert len(ordered) == len(deformers)
    assert all(got is want for got, want in zip(ordered, deformers)), (
        "没有父链时规范化必须原样交回（同一批对象、同一个顺序），否则今天的产物"
        "会被悄悄重排")


def test_parent_first_ordering_leaves_a_cyclic_table_alone():
    """成环的表不许被规范化悄悄改写：环要交给 ``deformer_space_map`` 报「成环」。

    拓扑排序遇到环时若「随便留一个顺序」，``test_warp_parent_cycle_is_refused``
    那条契约就会被一句误导性的槽位错误替掉。
    """
    from live2d_builder.exporter.moc3_model import parent_first_deformer_order
    a = WarpDeformerSpec("A", 1, 1, [DeformerGrid(0.0, _L0_LATTICE)],
                         parent_deformer_id="B")
    b = WarpDeformerSpec("B", 1, 1, [DeformerGrid(0.0, _L0_LATTICE)],
                         parent_deformer_id="A")
    ordered = parent_first_deformer_order([a, b])
    # 用 `is` 而不是 `==`：frozen dataclass 下 `==` 对「值相等的副本」也成立，
    # 规范化要是把条目复制一份再按别的顺序交回来，`==` 照样绿（姊妹用例用的就是 is）。
    assert len(ordered) == 2
    assert ordered[0] is a and ordered[1] is b, (
        "成环时必须原样交回**同一批对象**，顺序一个字都不许动")


# ======================================================================
# Task 3：单一头部场 —— ``ParamAngle*`` 在作者表里只能有一个头部持有者
#
# 自己的基线读数（在 36c3fcc4 重建歌手产物后用 tools/bindings_gap_probe.py 量，
# 满幅 ``ParamAngleZ=30`` + ``ParamBodyAngleZ=10``）：
#   eyelid_left↔eyewhite_left **27.1272px**、eyewhite_left↔nose 27.1268px、
#   cheek↔mouth 27.1267px、face↔eyewhite_left 27.1259px。
# 逐网格等效刚体拟合给出的原因是**圆心**而不是角度：脸/眉/口/鼻/发 θ=40.000°
# 圆心 (167.7, 1136.3)（网格自带键形带），眼/腮红/泪 θ=40.000° 圆心
# (198.4, 1161.5)（各条 warp 自己拟合的那一份）。角度一致、圆心差 39.7px ⇒
# 相对位移 θ(rad)·d ≈ 0.698×39.7 ≈ 27.7px —— 与实测的 27.13 同一回事。
# 只驱动 ParamAngleZ=30（身体轴停在默认值）时两侧圆心重合、头内部配对全为 0，
# 所以**验收必须在满幅上量**。
# ======================================================================

#: 取自 test_deformer_hierarchy_rotation.py 的真实分层产出名。
#: ``build()`` 会用 ``_matched_targets`` 按真实层名过滤 targets（deformers.py:783），
#: 夹具里不存在目标的变形器**整条不建**，所以断言作者意图要直接读类属性，
#: 断言"真建出来了"才走 build()。
SINGER_LAYERS = [
    "hair_back", "face", "eyelid_left", "eyelid_right",
    "eyewhite_left", "eyewhite_right", "irides_left", "irides_right",
    "hair_front", "eyebrow_L", "eyebrow_R", "nose", "mouth_cavity", "mouth",
    "neck", "clothes_top", "waistband", "collar", "cheek", "tear_left",
    "accessories",
]


def _authored_table():
    from live2d_builder.bones.deformers import DeformerHierarchy
    return list(DeformerHierarchy.DEFAULT_WARP_DEFORMERS)


def _authored(name):
    return next(e for e in _authored_table() if e["name"] == name)


def _built(names=None):
    from live2d_builder.bones.deformers import DeformerHierarchy
    names = list(SINGER_LAYERS if names is None else names)
    meshes = {n: {"vertices": [[0, 0], [10, 0], [10, 10], [0, 10]]}
              for n in names}
    return {d["name"]: d for d in DeformerHierarchy().build(
        {}, meshes, layer_names=names)["deformers"]}


def _authored_depth(name, entries=None):
    """沿**合法** under 边数到根级的层数（根级 = 1）。"""
    from live2d_builder.exporter.moc3_pipeline import resolve_deformer_parents
    entries = list(_authored_table() if entries is None else entries)
    parents = resolve_deformer_parents(entries)
    cursor, depth, guard = name, 1, 0
    while parents.get(cursor) and guard <= len(entries):
        cursor, depth, guard = parents[cursor], depth + 1, guard + 1
    return depth


def test_head_rig_is_authored_as_the_only_head_rotation_owner():
    head = _authored("HeadRig")
    assert head["profile"] == "head"
    assert head["parameters"] == ["ParamAngleX", "ParamAngleY", "ParamAngleZ"]
    assert {"face", "nose", "accessories"} <= set(head["targets"])
    # 头部三轴在**作者意图**里只出现在 HeadRig 一条上。基线这里会是
    # ['EyeBlink_L', 'EyeBlink_R', 'CheekFollow', 'TearFollow'] —— 四处各自再
    # 拟合一份，就是头内部那 27.1272px 的来源。
    # Task 4 引入 NeckBand（分级场，不是第二份刚体转动）以后这条要改成
    # ``== ["NeckBand"]``。
    refitters = [e["name"] for e in _authored_table()
                 if e["name"] != "HeadRig"
                 and "ParamAngleZ" in (e.get("parameters") or [])]
    assert refitters == [], refitters


def test_expression_deformers_inherit_head_instead_of_refitting_it():
    for name in ("BrowMove_L", "BrowMove_R", "MouthOpen", "MouthCavity",
                 "EyeBlink_L", "EyeBlink_R", "HairFrontSwing", "HairBackSwing",
                 "HairSideSwing_L", "HairSideSwing_R"):
        assert _authored(name)["under"] == "HeadRig", name
    assert "ParamAngleZ" not in (_authored("EyeBlink_L").get("parameters") or [])
    assert "ParamAngleZ" not in (_authored("HairFrontSwing").get("parameters") or [])
    # 眼睛退回 ``eye``：眨眼是本来就该相对父级动的局部表情，头部三轴由继承送到。
    # ``DEFORMER_PROFILES["eye_head"]`` 作为**能力**保留（见 TestEyeFollowsHead），
    # 只是作者表不再使用它。
    assert _authored("EyeBlink_L")["profile"] == "eye"
    assert _authored("EyeBlink_L")["parameters"] == ["ParamEyeLOpen"]
    assert _authored("EyeBlink_R")["parameters"] == ["ParamEyeROpen"]
    # 头是挂在身子上的：HeadRig 在躯干场之下（Task 4 的颈胸过渡也要这条边）
    assert _authored("HeadRig")["under"] == "BodySway"


def test_cheek_and_tear_no_longer_need_their_own_head_field():
    assert all(e["name"] not in ("CheekFollow", "TearFollow")
               for e in _authored_table())
    built = _built()
    assert "HeadRig" in built, "真实层名下 HeadRig 必须真的建出来（否则重蹈 rotation 的覆辙）"
    assert built["HeadRig"]["profile"] == "head"
    assert set(built["HeadRig"]["targets"]) >= {"face", "nose", "accessories"}


def test_fade_layers_are_listed_by_layer_name_in_the_head_rig_targets():
    """``cheek`` / ``tear_*`` 必须**显式列名**在 ``HeadRig.targets`` 里。

    淡入层的层名只活在 ``OPACITY_KEYFORM_LAYERS``（moc3_pipeline.py:1084-1088）；
    CheekFollow/TearFollow 一删，若不把层名交给 HeadRig，``_matched_targets``
    （deformers.py:783）就**静默**把它们丢在头场之外 —— 重演「腮红留在原地滑出脸外」
    （实测 ParamAngleZ=30 时 cheek 位移 0px），而 deformers.py:778 记过同款事故：
    同一批图 11 个变形器 → 0 个，构建照样报 valid。测试全绿也不代表画面没坏。
    """
    names = SINGER_LAYERS + ["tear_right"]
    built = _built(names)
    targets = set(built["HeadRig"]["targets"])
    assert {"cheek", "tear_left", "tear_right"} <= targets, targets
    from live2d_builder.exporter.moc3_pipeline import resolve_membership
    owned = resolve_membership(list(built.values()), names)
    assert owned["cheek"] == "HeadRig"
    assert owned["tear_left"] == "HeadRig"
    assert owned["tear_right"] == "HeadRig"


def test_head_rig_also_carries_the_eyelids_that_have_no_other_deformer():
    """``eyelid_left`` / ``eyelid_right`` 今天谁都不挂，只靠自己的键形带随头转。

    基线上 ``eyelid_left↔eyewhite_left`` = 27.1272px 正是这一对的开口：不把它们
    列进 HeadRig.targets，撤掉烘焙以后眼皮会**原地不动**，比现状更糟。
    """
    built = _built()
    assert {"eyelid_left", "eyelid_right"} <= set(built["HeadRig"]["targets"])
    from live2d_builder.exporter.moc3_pipeline import resolve_membership
    owned = resolve_membership(list(built.values()), SINGER_LAYERS)
    assert owned["eyelid_left"] == "HeadRig"


def test_body_sway_members_stay_on_body_sway_in_task_3():
    """neck / collar / clothes_top / waistband 仍归 BodySway，且没被 HeadRig 抢走。

    BodySway 那条带里**不含** ``ParamAngleZ``，撤掉这些网格自己的烘焙会让脖子彻底
    不跟头；把 neck 移交 NeckBand 必须与 NeckBand 的出现在同一笔提交里（Task 4），
    分两笔会留下「neck 谁都不挂」的中间态，那一刻跑的任何读数都是假的。
    """
    built = _built()
    assert {"neck", "clothes_top", "waistband", "collar"} <= set(
        built["BodySway"]["targets"])
    assert "under" not in built["BodySway"]
    from live2d_builder.exporter.moc3_pipeline import resolve_membership
    owned = resolve_membership(list(built.values()), SINGER_LAYERS)
    assert owned["neck"] == "BodySway"
    assert owned["collar"] == "BodySway"


def test_authored_head_field_chain_stops_at_the_measured_depth_three():
    """Task 1.5 只用官方 Core 量到**深度 3**，作者表今天就停在 3，一步不许多。

    量过的包络是 BodySway(1) → HeadRig(2) → MouthOpen / BrowMove_L / EyeBlink_L(3)。
    再加一级（``HairFrontSwing → HairTip`` 那种）之前必须先补一条深度 ≥4 的恒等链
    静止 + 键形继承用例并**跑官方 Core 量出来**；通不过就把那级压平并如实记
    skipped。这条用例就是那个前置条件的闸门 —— 未验证的深度不许静默进产物。
    """
    assert _authored_depth("BodySway") == 1
    assert _authored_depth("HeadRig") == 2
    for name in ("MouthOpen", "BrowMove_L", "EyeBlink_L", "HairFrontSwing",
                 "MouthCavity", "HairBackSwing"):
        assert _authored_depth(name) == 3, name
    deepest = max(_authored_depth(e["name"]) for e in _authored_table())
    assert deepest <= 3, deepest


def test_build_deformers_leaves_a_note_for_an_undeclared_under():
    """``under`` 拼错时必须有痕。

    ``resolve_deformer_parents`` 在 ``RigSpec._validate_deformers`` **之前**就把未声明
    父级滤掉了，所以 ``under: "HeadRgg"`` 今天会静默变成一片扁平林：既不报错、
    也不进 ``uncompiled``，画面还看着合理 —— 违反本分支「宁可不驱动…并如实留痕」。
    """
    meshes = [_patch("face"), _patch("mouth")]
    entries = [
        _entry("HeadRig", [], "head", ["face"]),
        _entry("MouthOpen", [], "mouth", ["mouth"], under="HeadRgg"),
    ]
    deformers, notes, _static = _build(meshes, entries, [])
    by_id = {d.deformer_id: d for d in deformers}
    assert by_id["MouthOpen"].parent_deformer_id == ""
    assert "MouthOpen(under HeadRgg 未声明：按无父级处理)" in notes, notes


def test_build_deformers_leaves_a_note_for_a_cyclic_under():
    """成环的 under 同样要留痕：环交给 ``deformer_space_map`` 报错，但作者得看得见解释。"""
    meshes = [_patch("face"), _patch("mouth")]
    entries = [
        _entry("Dee", [], "mouth", ["mouth"], under="Dum"),
        _entry("Dum", [], "mouth", ["face"], under="Dee"),
    ]
    deformers, notes, _static = _build(meshes, entries, [])
    assert all(d.parent_deformer_id == "" for d in deformers)
    assert any("父链成环" in n for n in notes), notes
    assert any(n.startswith("Dee(") for n in notes), notes


def test_undeclared_under_is_visible_from_the_authored_table_end_to_end(monkeypatch):
    """端到端：拼错的 ``under`` 走完 build() → build_deformers 还得留下同一条说明。

    只在函数级测不够 —— ``create_warp_deformer`` 用固定键集重建条目字典，键在那里
    掉了的话 ``build()`` 交出来的条目根本没有 "HeadRgg"，扁平林照样静默。
    """
    from live2d_builder.bones.deformers import DeformerHierarchy
    authored = [
        {"name": "HeadRig", "parent": "Head", "rows": 3, "cols": 3,
         "type": "warp", "profile": "head", "targets": ["face"]},
        {"name": "MouthOpen", "parent": "Mouth", "rows": 2, "cols": 3,
         "type": "warp", "profile": "mouth", "targets": ["mouth"],
         "under": "HeadRgg"},
    ]
    monkeypatch.setattr(DeformerHierarchy, "DEFAULT_WARP_DEFORMERS", authored)
    names = ["face", "mouth"]
    built = DeformerHierarchy().build(
        {}, {n: {"vertices": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0],
                              [0.0, 10.0]]} for n in names}, layer_names=names)
    assert built["by_name"]["MouthOpen"]["under"] == "HeadRgg"
    deformers, notes, _static = _build([_patch("face"), _patch("mouth")],
                                       built["deformers"], [])
    assert all(d.parent_deformer_id == "" for d in deformers)
    assert any("HeadRgg 未声明" in n for n in notes), notes


def test_build_deformers_flattens_a_chain_deeper_than_the_cap():
    """链深超过 ``TRUNK_MAX_CHAIN_DEPTH`` 时退回无父级并留痕。

    编译侧今天完全没上限：``resolve_deformer_parents`` 无界走，
    ``deformer_space_map.entry`` 只防环，而离线尺子与探针各自限在 ``guard < 32``。
    守卫要落在**编译侧**，而不是让递归去撞栈或撞探针的那个 32。
    """
    from live2d_builder.exporter.moc3_pipeline import TRUNK_MAX_CHAIN_DEPTH
    count = TRUNK_MAX_CHAIN_DEPTH + 2
    meshes = [_patch(f"m{i}") for i in range(count)]
    entries = [_entry("L1", [], "mouth", [meshes[0].mesh_id])]
    for i in range(2, count + 1):
        entries.append(_entry(f"L{i}", [], "mouth", [meshes[i - 1].mesh_id],
                              under=f"L{i - 1}"))
    deformers, notes, _static = _build(meshes, entries, [])
    by_id = {d.deformer_id: d for d in deformers}
    kept = [f"L{i}" for i in range(1, TRUNK_MAX_CHAIN_DEPTH + 1)]
    dropped = [f"L{i}" for i in range(TRUNK_MAX_CHAIN_DEPTH + 1, count + 1)]
    for name in dropped:
        assert by_id[name].parent_deformer_id == "", name
        assert any(name in n and "链深" in n for n in notes), (name, notes)
    assert by_id["L2"].parent_deformer_id == "L1"
    assert by_id[kept[-1]].parent_deformer_id == f"L{TRUNK_MAX_CHAIN_DEPTH - 1}"
    # 留下的链里最深的节点不许超过上限
    for name in kept:
        assert _authored_depth(name, entries) <= TRUNK_MAX_CHAIN_DEPTH or \
            by_id[name].parent_deformer_id == ""

# ======================================================================
# Task 3 Step B：父级的静止矩形要铺满**子孙**的画
#
# `build_deformers` 里 `rect = _member_rect(members)` 只看**直接**成员。挂上
# `under` 以后，子级的画不再出现在父级的成员表里，于是「画在父级网格之外」变成
# 常态（歌手件：hair_back 有 27/140 个顶点在 HeadRig 矩形之外，越界 74.85px）。
#
# 为什么这仍然要修，即使 Core 会外推（#72 已经量过）：官方美术约定是**画留在父级
# 网格之内**，只有留在里面，官方 Core、自研播放器、离线尺子才读同一个数 ——
# 矩形之内三方一致到 ≤1px，而越界 + 非仿射弯曲的父级键形实测差 29px
# （诊断 `H_out_4x3_bend`：夹取 118px / 外推 29px）。把画留在里面才是让三方
# 一致的那个修法；让尺子学会外推只是不再拿幻影当证据。
# ======================================================================

def _rect_of(deformer, params=()):
    """写出层眼里的「静止形」矩形（模型像素）—— 与内核换算 (s,t) 用的是同一个。"""
    return reference_rect(deformer, {p.parameter_id: p for p in params})


def _covers(rect, mesh):
    return [(x, y) for x, y in mesh.vertices
            if not (rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3])]


def test_a_parent_rect_covers_every_descendants_vertex():
    """孙级（三级链）的画也算进父级矩形：只看直接成员会把头发甩在 HeadRig 外面。"""
    face = _patch("face", 0.0, 120.0, 40.0, 200.0)           # HeadRig 自己的成员
    hair_root = _patch("hair_front", -20.0, 60.0, 60.0, 120.0)
    hair_tip = _patch("hair_back", -50.0, -260.0, 50.0, 40.0)  # 发梢远远低过头
    entries = [
        _entry("HeadRig", [], "", ["face"]),
        _entry("HairFrontSwing", [], "", ["hair_front"], under="HeadRig"),
        _entry("HairBackSwing", [], "", ["hair_back"], under="HairFrontSwing"),
    ]
    deformers, _notes, _static = _build([face, hair_root, hair_tip], entries, [])
    by_id = {d.deformer_id: d for d in deformers}
    assert by_id["HairBackSwing"].parent_deformer_id == "HairFrontSwing"
    rect = _rect_of(by_id["HeadRig"])
    for mesh in (face, hair_root, hair_tip):
        assert _covers(rect, mesh) == [], (mesh.mesh_id, rect)


def test_a_childless_deformer_rect_is_the_member_rect_unchanged():
    """没有子孙时一个字节都不许动：今天 13/13 都是根级，黄金回归钉的就是那份矩形。

    `tests/unit/test_moc3_deformers.py` 与 69b39399 那条根级黄金夹具覆盖的就是
    这条路径 —— 矩形并集只在**真的有 warp 子孙**时才生效。
    """
    from live2d_builder.exporter.moc3_pipeline import _member_rect
    face = _patch("face", 0.0, 120.0, 40.0, 200.0)
    deformers, _notes, _static = _build([face], [_entry("HeadRig", [], "", ["face"])], [])
    assert _rect_of(deformers[0]) == pytest.approx(_member_rect([face]))


def test_the_rect_union_follows_the_landed_parents_only():
    """父级没落地（没成员）时，子级的画不该被算进一个根本不存在的父级矩形里。

    `resolve_deformer_parents` 只看**声明**，而矩形服务的是**编译出来的**那条链：
    照声明并集的话，一个从未落地的父级会拿到一份它用不上的大矩形。
    """
    hair = _patch("hair_back", -50.0, -260.0, 50.0, 40.0)
    entries = [
        _entry("Ghost", [], "", []),                                  # 没有成员 → 不编译
        _entry("HairBackSwing", [], "", ["hair_back"], under="Ghost"),
    ]
    deformers, notes, _static = _build([hair], entries, [])
    assert [d.deformer_id for d in deformers] == ["HairBackSwing"]
    assert any("Ghost" in n for n in notes), notes
    assert _covers(_rect_of(deformers[0]), hair) == []
