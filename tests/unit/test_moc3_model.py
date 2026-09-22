"""通用静态编译器：计数派生、规模、非法输入必须被拒绝。"""
import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    MeshSpec,
    ParameterSpec,
    RigSpec,
    UnsupportedRig,
    compile_static_rig,
)


def _grid(name, cells=3, offset=0.0):
    """生成 cells*cells 的网格片：顶点数 (cells+1)^2，三角形数 2*cells^2。"""
    step = 1.0 / cells
    verts, uvs = [], []
    for row in range(cells + 1):
        for col in range(cells + 1):
            x, y = col * step, row * step
            verts.append((x * 100.0 + offset, y * 100.0 + offset))
            uvs.append((x, y))
    tris = []
    for row in range(cells):
        for col in range(cells):
            a = row * (cells + 1) + col
            b = a + 1
            c = a + cells + 1
            d = c + 1
            tris += [(a, c, b), (b, c, d)]
    return MeshSpec(mesh_id=name, vertices=verts, triangles=tris, uvs=uvs,
                    draw_order=float(offset))


def _rig(mesh_count=3, param_count=2):
    meshes = [_grid(f"ArtMesh{i}", cells=2 + i, offset=float(i * 10))
              for i in range(mesh_count)]
    params = [ParameterSpec(f"Param{i}") for i in range(param_count)]
    return RigSpec(meshes=meshes, parameters=params)


def test_compiles_clean_and_scales():
    spec = _rig(mesh_count=4, param_count=28)
    doc = compile_static_rig(spec)
    assert lint_document(doc) == []
    assert doc.counts[ms.CountIdx.ART_MESHES] == 4
    assert doc.counts[ms.CountIdx.PARAMETERS] == 28
    assert doc.counts[ms.CountIdx.UVS] == 2 * sum(
        len(m.vertices) for m in spec.meshes)
    assert doc.counts[ms.CountIdx.POSITION_INDICES] == 3 * sum(
        len(m.triangles) for m in spec.meshes)


def test_index_counts_use_official_field_semantics():
    """position_index_counts = 顶点数；vertex_counts = 索引条目数。"""
    mesh = _grid("ArtMesh0", cells=3)
    doc = compile_static_rig(RigSpec(meshes=[mesh]))
    assert doc.get("art_mesh.position_index_counts") == [len(mesh.vertices)]
    assert doc.get("art_mesh.vertex_counts") == [len(mesh.triangles) * 3]
    assert len(doc.get("position_index.indices")) == len(mesh.triangles) * 3


def test_every_object_references_an_existing_band():
    doc = compile_static_rig(_rig())
    bands = doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS]
    for holder in ("part.keyform_binding_band_indices",
                   "art_mesh.keyform_binding_band_indices"):
        assert all(0 <= v < bands for v in doc.get(holder)), holder


def test_geometry_is_globally_tiled_without_gaps():
    doc = compile_static_rig(_rig(mesh_count=5))
    begins = doc.get("art_mesh.uv_begin_indices")
    verts = doc.get("art_mesh.position_index_counts")
    assert begins == [sum(2 * v for v in verts[:i]) for i in range(len(verts))]
    # 关键形位置与 uv 一样逐顶点占 2 个浮点数，但每个关键形的起点按
    # 16 个浮点数（64 字节）对齐 —— 与官方导出器一致（Haru 369/369 核实）
    kpb = doc.get("art_mesh_keyform.keyform_position_begin_indices")
    assert all(b % 16 == 0 for b in kpb), kpb
    positions = doc.get("keyform_position.xys")
    for i, (begin, v) in enumerate(zip(kpb, verts)):
        assert begin + 2 * v <= len(positions)
        nxt = kpb[i + 1] if i + 1 < len(kpb) else len(positions)
        assert nxt - begin >= 2 * v


def test_canvas_origin_is_center():
    doc = compile_static_rig(_rig())
    assert doc.canvas.origin_x == doc.canvas.canvas_width / 2.0
    assert doc.canvas.origin_y == doc.canvas.canvas_height / 2.0


def test_roundtrip_preserves_identity(tmp_path):
    Moc3 = pytest.importorskip("moc3").Moc3
    spec = _rig(mesh_count=3, param_count=2)
    doc = compile_static_rig(spec)
    p = tmp_path / "rig.moc3"
    p.write_bytes(doc.to_bytes())
    back = Moc3.from_file(str(p))
    assert back["art_mesh.ids"] == [m.mesh_id for m in spec.meshes]
    assert back["parameter.ids"] == [q.parameter_id for q in spec.parameters]
    assert back["part.ids"] == [m.effective_part_id for m in spec.meshes]


def test_deformers_are_refused_not_dropped():
    with pytest.raises(UnsupportedRig, match="变形器"):
        compile_static_rig(RigSpec(meshes=[_grid("A")], deformer_count=3))


def test_empty_rig_is_refused():
    with pytest.raises(UnsupportedRig, match="至少需要一个网格"):
        compile_static_rig(RigSpec(meshes=[]))


@pytest.mark.parametrize("mutate, message", [
    (lambda m: MeshSpec(m.mesh_id, m.vertices, m.triangles, m.uvs[:-1]),
     "与顶点数"),
    (lambda m: MeshSpec(m.mesh_id, m.vertices,
                        list(m.triangles) + [(0, 1, 2, 3)], m.uvs),
     "不是三元组"),
    (lambda m: MeshSpec(m.mesh_id, m.vertices,
                        list(m.triangles) + [(0, 1, 9999)], m.uvs), "越界"),
    (lambda m: MeshSpec(m.mesh_id, m.vertices[:2], [], m.uvs[:2]), "顶点数"),
])
def test_bad_mesh_geometry_is_rejected(mutate, message):
    mesh = _grid("A", cells=3)
    with pytest.raises(UnsupportedRig, match=message):
        compile_static_rig(RigSpec(meshes=[mutate(mesh)]))


def test_non_finite_vertex_is_rejected():
    mesh = _grid("A", cells=2)
    bad = list(mesh.vertices)
    bad[0] = (float("nan"), 0.0)
    with pytest.raises(UnsupportedRig, match="非有限值"):
        compile_static_rig(RigSpec(meshes=[
            MeshSpec(mesh.mesh_id, bad, mesh.triangles, mesh.uvs)]))


def test_vertex_index_width_limit():
    """position_index 是有符号 16 位，超限必须拒绝而不是写出越界索引。"""
    limit = 32768
    verts = [(float(i), 0.0) for i in range(limit)]
    uvs = [(0.0, 0.0)] * limit
    with pytest.raises(UnsupportedRig, match="16 位"):
        compile_static_rig(RigSpec(meshes=[
            MeshSpec("A", verts, [(0, 1, 2)], uvs)]))


@pytest.mark.parametrize("param", [
    ParameterSpec("P", minimum=30.0, maximum=-30.0),
    ParameterSpec("P", minimum=-10.0, maximum=10.0, default=99.0),
])
def test_out_of_range_parameter_is_rejected(param):
    with pytest.raises(UnsupportedRig):
        compile_static_rig(RigSpec(meshes=[_grid("A")], parameters=[param]))


def test_duplicate_ids_are_rejected():
    with pytest.raises(UnsupportedRig, match="重复"):
        compile_static_rig(RigSpec(meshes=[_grid("A"), _grid("A")]))
    with pytest.raises(UnsupportedRig, match="重复"):
        compile_static_rig(RigSpec(
            meshes=[_grid("A")],
            parameters=[ParameterSpec("P"), ParameterSpec("P")]))
