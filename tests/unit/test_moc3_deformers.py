"""warp 变形器编译：顶点换算成 (s, t)、additional 段补齐、非法参考网格要拒绝。

这里的每条结构断言都对应一次官方 Cubism Core 实测（见
tests/integration/test_moc3_deformer_acceptance.py 与 tools/probe_warp_st_identity.py）。
"""
import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
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
