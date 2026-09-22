"""最小模型构建：计数一致 + 绑定链完整 + 参考读回往返。"""
import struct

import pytest

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_builder import (
    MinimalModelSpec,
    build_minimal_model,
)


def _spec() -> MinimalModelSpec:
    return MinimalModelSpec(
        part_id="Part1",
        art_mesh_id="ArtMesh1",
        parameter_id="ParamAngleX",
        width=512.0,
        height=512.0,
    )


def test_counts_are_set_for_declared_parts():
    doc = build_minimal_model(_spec())
    assert doc.counts[ms.CountIdx.PARTS] == 1
    assert doc.counts[ms.CountIdx.ART_MESHES] == 1
    assert doc.counts[ms.CountIdx.PARAMETERS] == 1
    assert doc.counts[ms.CountIdx.PART_KEYFORMS] == 1
    # 关键形数 == 带内绑定键数之积（官方 Haru 核实）：3 个键 -> 3 个关键形
    assert doc.counts[ms.CountIdx.ART_MESH_KEYFORMS] == 3
    assert doc.get("art_mesh.keyform_counts") == [3]
    assert doc.counts[ms.CountIdx.UVS] == 8


def test_uv_count_is_floats_not_vertices():
    doc = build_minimal_model(_spec())
    assert len(doc.get("uv.xys")) == 8
    assert doc.counts[ms.CountIdx.UVS] == 8


def test_binding_chain_is_complete():
    doc = build_minimal_model(_spec())
    assert doc.counts[ms.CountIdx.KEYFORM_BINDINGS] == 1
    # 官方约定：band 0 是未被参数驱动对象共用的空带，实际绑定另起一带
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS] == 2
    assert doc.counts[ms.CountIdx.KEYFORM_BINDING_INDICES] == 1
    assert doc.get("keys.values"), "参数必须有可插值的键值"
    assert doc.get("draw_order_group.object_counts"), "必须有绘制顺序组"


def test_every_object_references_an_existing_band():
    """官方数据从不使用 -1 带索引；越界或悬空的带索引会被内核判为非法。"""
    doc = build_minimal_model(_spec())
    bands = doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS]
    for holder in ("part.keyform_binding_band_indices",
                   "art_mesh.keyform_binding_band_indices"):
        for value in doc.get(holder):
            assert 0 <= value < bands, f"{holder} 引用了不存在的带 {value}"
    # 带内绑定索引必须落在 keyform_binding_index 范围内
    begins = doc.get("keyform_binding_band.begin_indices")
    counts = doc.get("keyform_binding_band.counts")
    limit = len(doc.get("keyform_binding_index.indices"))
    assert sum(counts) == limit
    for begin, count in zip(begins, counts):
        assert 0 <= begin and begin + count <= limit


def test_keyform_positions_cover_every_vertex():
    doc = build_minimal_model(_spec())
    positions = doc.get("keyform_position.xys")
    assert len(positions) % 2 == 0
    assert len(positions) == doc.counts[ms.CountIdx.KEYFORM_POSITIONS]


def test_roundtrip_through_reference_reader(tmp_path):
    Moc3 = pytest.importorskip("moc3").Moc3
    doc = build_minimal_model(_spec())
    p = tmp_path / "model.moc3"
    p.write_bytes(doc.to_bytes())
    back = Moc3.from_file(str(p))
    assert back["part.ids"] == ["Part1"]
    assert back["art_mesh.ids"] == ["ArtMesh1"]
    assert back["parameter.ids"] == ["ParamAngleX"]
    assert back["uv.xys"] == doc.get("uv.xys")
    assert back["position_index.indices"] == doc.get("position_index.indices")


def test_reserialize_is_byte_identical(tmp_path):
    Moc3 = pytest.importorskip("moc3").Moc3
    doc = build_minimal_model(_spec())
    first = doc.to_bytes()
    p = tmp_path / "m.moc3"
    p.write_bytes(first)
    assert Moc3.from_file(str(p)).to_bytes() == first
