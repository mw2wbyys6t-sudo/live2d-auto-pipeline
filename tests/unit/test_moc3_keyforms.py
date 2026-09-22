"""参数形变键型编译：绑定链、逐键形状、非法输入必须被拒绝。

所有断言对应的结构关系均已对官方 Haru.moc3 全量核实
（见 tools/probe_haru_keyforms.py 的输出）。
"""
import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    KeyformShape,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    UnsupportedRig,
    compile_static_rig,
)


def _grid(name, cells=2, offset=0.0):
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


def _shifted(mesh, key_value):
    """键值下把网格整体平移，作为该键的形状。"""
    return KeyformShape(
        key_value=key_value,
        vertices=[(x + key_value, y) for x, y in mesh.vertices])


def _driven(bindings, mesh_count=2, cells=2):
    """前 len(bindings) 个网格按 (参数, 键值序列) 驱动，其余保持静态。"""
    meshes = [_grid(f"ArtMesh{i}", cells=cells, offset=float(i * 10))
              for i in range(mesh_count)]
    driven = []
    for mesh, (pid, keys) in zip(meshes, bindings):
        driven.append(MeshSpec(
            mesh_id=mesh.mesh_id, vertices=mesh.vertices,
            triangles=mesh.triangles, uvs=mesh.uvs,
            draw_order=mesh.draw_order,
            keyform_parameter_id=pid,
            keyform_shapes=[_shifted(mesh, k) for k in keys]))
    params = [ParameterSpec(pid) for pid in ("Param0", "Param1")]
    return RigSpec(meshes=driven + meshes[len(bindings):], parameters=params)


def test_keyform_binding_chain_matches_official_shape():
    doc = compile_static_rig(_driven([("Param0", (-30.0, 0.0, 30.0))]))
    # band 0 是空带，被驱动参数另起 band 1；binding 与参数一一对应
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS] == 2
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 1
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_INDICES] == 1
    assert doc.counts[ms.CountIdx.KEYS] == 3
    assert doc.get("keys.values") == [-30.0, 0.0, 30.0]
    assert doc.get("keyform_binding.keys_counts") == [3]
    assert doc.get("keyform_binding.keys_begin_indices") == [0]
    assert doc.get("keyform_binding_index.indices") == [0]
    assert doc.get("keyform_binding_band.begin_indices") == [0, 0]
    assert doc.get("keyform_binding_band.counts") == [0, 1]
    # 驱动网格引用 band 1，静态网格引用空带 0
    assert doc.get("art_mesh.keyform_binding_band_indices") == [1, 0]
    # 关键形数 == 带内键数之积（官方 Haru 84/84 网格核实）
    assert doc.get("art_mesh.keyform_counts") == [3, 1]
    assert doc.get("art_mesh.keyform_begin_indices") == [0, 3]
    assert doc.counts[ms.CountIdx.ART_MESH_KEYFORMS] == 4
    # 参数侧：Param0 拥有 binding 0；Param1 只声明不绑定
    assert doc.get("parameter.keyform_binding_begin_indices") == [0, 0]
    assert doc.get("parameter.keyform_binding_counts") == [1, 0]
    assert lint_document(doc) == []


def test_each_keyform_carries_its_own_shape():
    spec = _driven([("Param0", (-30.0, 0.0, 30.0))])
    doc = compile_static_rig(spec)
    begins = doc.get("art_mesh_keyform.keyform_position_begin_indices")
    positions = doc.get("keyform_position.xys")
    ppu = spec.pixels_per_unit
    mesh = spec.meshes[0]
    for k, shape in enumerate(mesh.keyform_shapes):
        flat = [v / ppu for xy in shape.vertices for v in xy]
        begin = begins[k]
        assert positions[begin: begin + len(flat)] == pytest.approx(flat)
    # 静态网格的关键形用的是它自己的顶点（同样换算成单位坐标）
    static_mesh = spec.meshes[1]
    static_begin = begins[len(mesh.keyform_shapes)]
    static_flat = [v / ppu for xy in static_mesh.vertices for v in xy]
    assert positions[static_begin: static_begin + len(static_flat)] == \
        pytest.approx(static_flat)


def test_positions_are_stored_in_units_not_canvas_pixels():
    """RigSpec 收画布像素，moc3 存单位坐标：差一个 pixels_per_unit。"""
    verts, uvs, tris = _grid("A", cells=2).vertices, _grid("A", cells=2).uvs, \
        _grid("A", cells=2).triangles
    spec = RigSpec(meshes=[MeshSpec("A", verts, tris, uvs)],
                   pixels_per_unit=250.0)
    doc = compile_static_rig(spec)
    stored = doc.get("keyform_position.xys")
    expect = [v / 250.0 for xy in verts for v in xy]
    assert stored[:len(expect)] == pytest.approx(expect)
    # 画布字段仍是像素
    assert doc.canvas.canvas_width == spec.canvas_width
    assert doc.canvas.origin_x == spec.canvas_width / 2.0


def test_two_parameters_get_separate_bands_and_bindings():
    doc = compile_static_rig(_driven([
        ("Param0", (-30.0, 30.0)),
        ("Param1", (0.0, 1.0)),
    ]))
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS] == 3
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 2
    assert doc.get("art_mesh.keyform_binding_band_indices") == [1, 2]
    assert doc.get("art_mesh.keyform_counts") == [2, 2]
    # binding 编号按参数声明顺序，与 parameter 侧的划分一致
    assert doc.get("parameter.keyform_binding_begin_indices") == [0, 1]
    assert doc.get("parameter.keyform_binding_counts") == [1, 1]
    assert doc.get("keys.values") == [-30.0, 30.0, 0.0, 1.0]
    assert doc.get("keyform_binding.keys_begin_indices") == [0, 2]
    assert lint_document(doc) == []


def test_meshes_sharing_a_parameter_share_band_and_binding():
    doc = compile_static_rig(_driven([
        ("Param0", (0.0, 1.0)),
        ("Param0", (0.0, 1.0)),
    ]))
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 1
    assert doc.get("art_mesh.keyform_binding_band_indices") == [1, 1]
    assert doc.get("parameter.keyform_binding_counts") == [1, 0]
    assert lint_document(doc) == []


def test_keyform_opacity_and_draw_order_are_per_keyform():
    mesh = _grid("A", cells=2)
    shapes = [
        KeyformShape(key_value=0.0, vertices=mesh.vertices, opacity=0.0),
        KeyformShape(key_value=1.0, vertices=mesh.vertices, opacity=1.0,
                     draw_order=7.0),
    ]
    spec = RigSpec(
        meshes=[MeshSpec("A", mesh.vertices, mesh.triangles, mesh.uvs,
                         opacity=0.5, draw_order=2.0,
                         keyform_parameter_id="P", keyform_shapes=shapes)],
        parameters=[ParameterSpec("P", minimum=0.0, maximum=1.0)])
    doc = compile_static_rig(spec)
    assert doc.get("art_mesh_keyform.opacities") == [0.0, 1.0]
    assert doc.get("art_mesh_keyform.draw_orders") == [2.0, 7.0]
    assert lint_document(doc) == []


def test_static_path_still_declares_parameters_without_bindings():
    meshes = [_grid(f"ArtMesh{i}") for i in range(3)]
    params = [ParameterSpec(f"Param{i}") for i in range(28)]
    doc = compile_static_rig(RigSpec(meshes=meshes, parameters=params))
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 0
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS] == 1
    assert doc.get("art_mesh.keyform_counts") == [1, 1, 1]
    assert doc.get("parameter.keyform_binding_counts") == [0] * 28
    assert lint_document(doc) == []


@pytest.mark.parametrize("parameter_id, keys, message", [
    ("", (-30.0, 30.0), "缺少 keyform_parameter_id"),
    ("Nope", (-30.0, 30.0), "未在 RigSpec.parameters 声明"),
    ("Param0", (0.0,), "至少需要 2 个键形"),
    ("Param0", (30.0, -30.0), "严格递增"),
    ("Param0", (0.0, 999.0), "超出参数"),
])
def test_bad_keyform_declaration_is_rejected(parameter_id, keys, message):
    mesh = _grid("A", cells=2)
    with pytest.raises(UnsupportedRig, match=message):
        compile_static_rig(RigSpec(
            meshes=[MeshSpec(
                "A", mesh.vertices, mesh.triangles, mesh.uvs,
                keyform_parameter_id=parameter_id,
                keyform_shapes=[_shifted(mesh, k) for k in keys])],
            parameters=[ParameterSpec("Param0")]))


def test_keyform_shape_vertex_count_must_match_mesh():
    mesh = _grid("A", cells=2)
    other = _grid("B", cells=3)
    with pytest.raises(UnsupportedRig, match="与网格顶点数"):
        compile_static_rig(RigSpec(
            meshes=[MeshSpec(
                "A", mesh.vertices, mesh.triangles, mesh.uvs,
                keyform_parameter_id="Param0",
                keyform_shapes=[KeyformShape(0.0, mesh.vertices),
                                KeyformShape(1.0, other.vertices)])],
            parameters=[ParameterSpec("Param0", minimum=0.0, maximum=1.0)]))


def test_non_finite_keyform_vertex_is_rejected():
    mesh = _grid("A", cells=2)
    bad = list(mesh.vertices)
    bad[1] = (float("inf"), 0.0)
    with pytest.raises(UnsupportedRig, match="非有限值"):
        compile_static_rig(RigSpec(
            meshes=[MeshSpec(
                "A", mesh.vertices, mesh.triangles, mesh.uvs,
                keyform_parameter_id="Param0",
                keyform_shapes=[KeyformShape(0.0, mesh.vertices),
                                KeyformShape(1.0, bad)])],
            parameters=[ParameterSpec("Param0", minimum=0.0, maximum=1.0)]))


def test_same_parameter_with_two_key_layouts_gets_two_bindings():
    """同一参数、不同键值布局 -> 两个 binding、两个带，编号在该参数名下连续。"""
    mesh0 = _grid("A0", cells=2)
    mesh1 = _grid("A1", cells=2)

    def driven(name, mesh, keys):
        return MeshSpec(name, mesh.vertices, mesh.triangles, mesh.uvs,
                        keyform_parameter_id="Param0",
                        keyform_shapes=[_shifted(mesh, k) for k in keys])

    spec = RigSpec(meshes=[driven("A0", mesh0, (-30.0, 30.0)),
                           driven("A1", mesh1, (0.0, 1.0)),
                           _grid("A2", cells=2)],
                  parameters=[ParameterSpec("Param0"), ParameterSpec("Param1")])
    doc = compile_static_rig(spec)

    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 2
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS] == 3
    assert doc.counts[ms.CountIdx.KEYS] == 4
    assert doc.get("keys.values") == [-30.0, 30.0, 0.0, 1.0]
    assert doc.get("keyform_binding.keys_counts") == [2, 2]
    assert doc.get("keyform_binding.keys_begin_indices") == [0, 2]
    # 两个网格各用各的带，第三个静态网格用空带 0
    assert doc.get("art_mesh.keyform_binding_band_indices") == [1, 2, 0]
    # 参数侧：Param0 名下是 binding [0,2)，Param1 无绑定
    assert doc.get("parameter.keyform_binding_begin_indices") == [0, 0]
    assert doc.get("parameter.keyform_binding_counts") == [2, 0]
    assert lint_document(doc) == []


def test_keyform_rig_roundtrips_through_reader(tmp_path):
    Moc3 = pytest.importorskip("moc3").Moc3
    doc = compile_static_rig(_driven([("Param0", (-30.0, 0.0, 30.0))]))
    path = tmp_path / "keyform.moc3"
    path.write_bytes(doc.to_bytes())
    back = Moc3.from_file(str(path))
    assert back["keys.values"] == [-30.0, 0.0, 30.0]
    assert back["art_mesh.keyform_counts"] == [3, 1]
    assert back["keyform_binding.keys_counts"] == [3]
    assert back["art_mesh.keyform_binding_band_indices"] == [1, 0]


def test_product_rule_violation_is_flagged_by_lint():
    """故意写出「3 个键但只有 1 个关键形」的旧结构，lint 必须拦下。"""
    doc = compile_static_rig(_driven([("Param0", (-30.0, 0.0, 30.0))]))
    doc.set("art_mesh.keyform_counts", [1, 1])
    doc.counts[ms.CountIdx.ART_MESH_KEYFORMS] = 2
    doc.set("art_mesh.keyform_begin_indices", [0, 1])
    doc.set("art_mesh_keyform.opacities", [1.0, 1.0])
    doc.set("art_mesh_keyform.draw_orders", [0.0, 0.0])
    doc.set("art_mesh_keyform.keyform_position_begin_indices", [0, 16])
    issues = lint_document(doc)
    assert any("关键形" in str(i) for i in issues), issues


def test_warp_deformer_compilation_is_covered_by_its_own_suite():
    """变形器已可编译，结构性断言移到 tests/unit/test_moc3_deformers.py。"""
    from live2d_builder.exporter.moc3_model import DeformerGrid, WarpDeformerSpec

    mesh = _grid("A", cells=2)
    deformer = WarpDeformerSpec(
        deformer_id="Warp1", rows=2, cols=2,
        grids=[DeformerGrid(0.0, [(float(x) * 100.0 / 2, float(y) * 100.0 / 2)
                                  for x in range(3) for y in range(3)])])
    spec = RigSpec(meshes=[MeshSpec(mesh.mesh_id, mesh.vertices, mesh.triangles,
                                    mesh.uvs, draw_order=mesh.draw_order,
                                    deformer_id="Warp1")],
                  deformers=[deformer])
    doc = compile_static_rig(spec)
    assert doc.counts[ms.CountIdx.DEFORMERS] == 1
    assert doc.counts[ms.CountIdx.WARP_DEFORMERS] == 1
    assert doc.get("additional.quad_transforms") == [0]
    assert lint_document(doc) == []
