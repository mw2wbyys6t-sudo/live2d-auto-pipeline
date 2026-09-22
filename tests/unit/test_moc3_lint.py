"""自洽性校验：长度不符、索引越界、悬空引用必须被拦截。"""
from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_builder import MinimalModelSpec, build_minimal_model
from live2d_builder.exporter.moc3_lint import lint_document


def _good():
    return build_minimal_model(MinimalModelSpec())


def test_clean_model_has_no_issues():
    assert lint_document(_good()) == []


def test_section_length_mismatch_is_reported():
    doc = _good()
    doc.set("part.ids", ["A", "B"])  # counts.PARTS 仍为 1
    issues = lint_document(doc)
    assert any("part.ids" in i.section for i in issues)


def test_out_of_range_begin_index_is_reported():
    doc = _good()
    doc.set("art_mesh.position_index_begin_indices", [999])
    issues = lint_document(doc)
    assert any("position_index_begin_indices" in i.section for i in issues)


def test_dangling_parent_part_index_is_reported():
    doc = _good()
    doc.set("art_mesh.parent_part_indices", [7])  # 只有 1 个部件
    issues = lint_document(doc)
    assert any("parent_part_indices" in i.section for i in issues)


def test_empty_binding_chain_is_reported():
    doc = _good()
    doc.set("keys.values", [])
    issues = lint_document(doc)
    assert any("keys.values" in i.section for i in issues)


def _deformer_doc(verts: int, rows: int = 2, cols: int = 2, spans=None):
    from live2d_builder.exporter.moc3_container import CanvasInfo, Moc3Container
    from live2d_builder.exporter import moc3_sections as ms

    doc = Moc3Container(version=ms.MocVersion.V3_03)
    doc.canvas = CanvasInfo(pixels_per_unit=100.0, origin_x=256.0, origin_y=256.0,
                            canvas_width=512.0, canvas_height=512.0)
    doc.counts[ms.CountIdx.WARP_DEFORMERS] = 1
    doc.counts[ms.CountIdx.WARP_DEFORMER_KEYFORMS] = 2 if spans else 1
    doc.set("warp_deformer.rows", [rows])
    doc.set("warp_deformer.cols", [cols])
    doc.set("warp_deformer.vertex_counts", [verts])
    doc.set("warp_deformer.keyform_begin_indices", [0])
    doc.set("warp_deformer.keyform_counts", [2 if spans else 1])
    begins = [0, spans] if spans else [0]
    doc.set("warp_deformer_keyform.keyform_position_begin_indices", begins)
    doc.set("warp_deformer_keyform.opacities", [1.0] * len(begins))
    doc.set("keyform_position.xys", [0.0] * (begins[-1] + 2 * verts))
    return doc


def test_deformer_grid_rules_fire_on_bad_values():
    from live2d_builder.exporter.moc3_lint import _check_deformer_grids

    # rows=2,cols=2 应有 9 个控制点；写 8 个必须报警
    issues = _check_deformer_grids(_deformer_doc(8))
    assert any(i.section == "warp_deformer.vertex_counts" for i in issues), issues

    # 相邻控制网格间距必须是 align16(2*9) = 32；写 18 要报警
    issues = _check_deformer_grids(_deformer_doc(9, spans=18))
    assert any("keyform_position_begin_indices" in i.section for i in issues), issues

    # 自洽的取值不应报警
    assert _check_deformer_grids(_deformer_doc(9, spans=32)) == []


def test_empty_additional_section_with_deformers_is_reported():
    """实测规则：V3_03 下 deformer 表非空而 additional 段为空 -> 内核判 Header invalid。"""
    from live2d_builder.exporter.moc3_lint import _check_additional_section

    doc = _deformer_doc(9)
    doc.counts[ms.CountIdx.DEFORMERS] = 1
    assert any(i.section == "additional.quad_transforms"
               for i in _check_additional_section(doc))
    doc.set("additional.quad_transforms", [0])
    assert _check_additional_section(doc) == []
    doc.counts[ms.CountIdx.DEFORMERS] = 2
    assert any("与变形器数" in i.message for i in _check_additional_section(doc))


def test_deformer_band_mismatch_is_reported():
    from live2d_builder.exporter.moc3_lint import _check_deformer_bands

    doc = _deformer_doc(9)
    doc.set("deformer.types", [0])
    doc.set("deformer.specific_indices", [0])
    doc.set("deformer.keyform_binding_band_indices", [1])
    doc.set("warp_deformer.keyform_binding_band_indices", [1])
    assert _check_deformer_bands(doc) == []
    doc.set("warp_deformer.keyform_binding_band_indices", [2])
    assert any(i.section == "deformer.keyform_binding_band_indices"
               for i in _check_deformer_bands(doc))
