"""warp 变形器编译：顶点换算成 (s, t)、additional 段补齐、非法参考网格要拒绝。

这里的每条结构断言都对应一次官方 Cubism Core 实测（见
tests/integration/test_moc3_deformer_acceptance.py 与 tools/probe_warp_st_identity.py）。
"""
import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
    KeyformShape,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    UnsupportedRig,
    WarpDeformerSpec,
    compile_static_rig,
    reference_rect,
    to_grid_st,
)

PPU = 100.0


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


def test_static_warp_stores_vertices_as_grid_coordinates():
    doc = compile_static_rig(_spec([_lattice()]))
    begin = doc.get("art_mesh_keyform.keyform_position_begin_indices")[0]
    # (s, t) 无量纲：不除 pixels_per_unit，且矩形参考网格下就是线性归一化
    assert _pool_positions(doc, begin, 4) == pytest.approx(
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
    """V3_03 下 deformer 表非空而 additional 段为空 -> 内核判 Header invalid。"""
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
    assert to_grid_st([(25.0, 50.0)], rect) == pytest.approx([(0.25, 0.5)])


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
    MOUTH_OPEN_STRETCH,
    build_deformers,
    driven_parameters,
    _attach_capabilities,
    _band_indices,
    _centered_fraction,
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


def _entry(name, parameters, profile, targets, rows=2, cols=3, mirror=False):
    out = {"name": name, "type": "warp", "parent": "Mouth",
           "grid_rows": rows, "grid_cols": cols,
           "parameters": list(parameters), "profile": profile,
           "targets": list(targets)}
    if mirror:
        out["mirror"] = True
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
