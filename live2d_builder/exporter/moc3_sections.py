"""moc3 二进制格式的 section 布局定义（自动生成，请勿手改）。

生成脚本: tools/extract_moc3_layout.py
数据来源: py-moc3 (MIT) 的 SECTION_LAYOUT，源自官方 Cubism SDK 导出器反编译。

容器布局:
    [0:64]      头部（magic b"MOC3" + 版本 + 端序）
    [64:704]    Section Offset Table，160 x uint32
    [704:1984]  零填充
    [1984:]     body = countInfo(128) + canvasInfo(64) + 各 section（逐个 64 字节对齐）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

MAGIC = b"MOC3"
HEADER_SIZE = 64
SOT_SIZE = 640
SOT_COUNT = 160
COUNT_INFO_SIZE = 128
COUNT_INFO_MAX = 23
DEFAULT_OFFSET = 1984
ALIGN = 64
RUNTIME_UNIT_SIZE = 8
CANVAS_BODY_SIZE = 64


class MocVersion:
    """moc3 格式版本（值与参考库一致，勿手改）。"""
    V3_00 = 1
    V3_03 = 2
    V4_00 = 3
    V4_02 = 4
    V5_00 = 5


ELEM_SIZES: Dict[str, int] = {
    "i32": 4,
    "f32": 4,
    "i16": 2,
    "u8": 1,
    "bool": 4,
    "str64": 64,
}


@dataclass(frozen=True)
class SectionEntry:
    """一个 section 的描述：名称、元素类型、计数来源、对齐、所属分组。"""
    name: str
    elem_type: str
    count_idx: int
    align: int
    group: str

    @property
    def elem_size(self) -> int:
        return ELEM_SIZES[self.elem_type]


class CountIdx:
    """count info 表索引（共 23 项，仅 23 项在用）。"""
    PARTS = 0
    DEFORMERS = 1
    WARP_DEFORMERS = 2
    ROTATION_DEFORMERS = 3
    ART_MESHES = 4
    PARAMETERS = 5
    PART_KEYFORMS = 6
    WARP_DEFORMER_KEYFORMS = 7
    ROTATION_DEFORMER_KEYFORMS = 8
    ART_MESH_KEYFORMS = 9
    KEYFORM_POSITIONS = 10
    KEYFORM_BINDING_INDICES = 11
    KEYFORM_BINDING_BANDS = 12
    KEYFORM_BINDINGS = 13
    KEYS = 14
    UVS = 15
    POSITION_INDICES = 16
    DRAWABLE_MASKS = 17
    DRAW_ORDER_GROUPS = 18
    DRAW_ORDER_GROUP_OBJECTS = 19
    GLUES = 20
    GLUE_INFOS = 21
    GLUE_KEYFORMS = 22


SECTION_LAYOUT: List[SectionEntry] = [
    SectionEntry(name="part.runtime_space", elem_type="runtime", count_idx=0, align=64, group="part"),
    SectionEntry(name="part.ids", elem_type="str64", count_idx=0, align=0, group="part"),
    SectionEntry(name="part.keyform_binding_band_indices", elem_type="i32", count_idx=0, align=64, group="part"),
    SectionEntry(name="part.keyform_begin_indices", elem_type="i32", count_idx=0, align=64, group="part"),
    SectionEntry(name="part.keyform_counts", elem_type="i32", count_idx=0, align=64, group="part"),
    SectionEntry(name="part.visibles", elem_type="bool", count_idx=0, align=64, group="part"),
    SectionEntry(name="part.enables", elem_type="bool", count_idx=0, align=64, group="part"),
    SectionEntry(name="part.parent_part_indices", elem_type="i32", count_idx=0, align=64, group="part"),
    SectionEntry(name="deformer.runtime_space", elem_type="runtime", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="deformer.ids", elem_type="str64", count_idx=1, align=0, group="deformer"),
    SectionEntry(name="deformer.keyform_binding_band_indices", elem_type="i32", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="deformer.visibles", elem_type="bool", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="deformer.enables", elem_type="bool", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="deformer.parent_part_indices", elem_type="i32", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="deformer.parent_deformer_indices", elem_type="i32", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="deformer.types", elem_type="i32", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="deformer.specific_indices", elem_type="i32", count_idx=1, align=64, group="deformer"),
    SectionEntry(name="warp_deformer.keyform_binding_band_indices", elem_type="i32", count_idx=2, align=64, group="warp_deformer"),
    SectionEntry(name="warp_deformer.keyform_begin_indices", elem_type="i32", count_idx=2, align=64, group="warp_deformer"),
    SectionEntry(name="warp_deformer.keyform_counts", elem_type="i32", count_idx=2, align=64, group="warp_deformer"),
    SectionEntry(name="warp_deformer.vertex_counts", elem_type="i32", count_idx=2, align=64, group="warp_deformer"),
    SectionEntry(name="warp_deformer.rows", elem_type="i32", count_idx=2, align=64, group="warp_deformer"),
    SectionEntry(name="warp_deformer.cols", elem_type="i32", count_idx=2, align=64, group="warp_deformer"),
    SectionEntry(name="rotation_deformer.keyform_binding_band_indices", elem_type="i32", count_idx=3, align=64, group="rotation_deformer"),
    SectionEntry(name="rotation_deformer.keyform_begin_indices", elem_type="i32", count_idx=3, align=64, group="rotation_deformer"),
    SectionEntry(name="rotation_deformer.keyform_counts", elem_type="i32", count_idx=3, align=64, group="rotation_deformer"),
    SectionEntry(name="rotation_deformer.base_angles", elem_type="f32", count_idx=3, align=64, group="rotation_deformer"),
    SectionEntry(name="art_mesh.runtime_space_0", elem_type="runtime", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.runtime_space_1", elem_type="runtime", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.runtime_space_2", elem_type="runtime", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.runtime_space_3", elem_type="runtime", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.ids", elem_type="str64", count_idx=4, align=0, group="art_mesh"),
    SectionEntry(name="art_mesh.keyform_binding_band_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.keyform_begin_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.keyform_counts", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.visibles", elem_type="bool", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.enables", elem_type="bool", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.parent_part_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.parent_deformer_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.texture_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.drawable_flags", elem_type="u8", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.position_index_counts", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.uv_begin_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.position_index_begin_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.vertex_counts", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.mask_begin_indices", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="art_mesh.mask_counts", elem_type="i32", count_idx=4, align=64, group="art_mesh"),
    SectionEntry(name="parameter.runtime_space", elem_type="runtime", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="parameter.ids", elem_type="str64", count_idx=5, align=0, group="parameter"),
    SectionEntry(name="parameter.max_values", elem_type="f32", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="parameter.min_values", elem_type="f32", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="parameter.default_values", elem_type="f32", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="parameter.repeats", elem_type="bool", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="parameter.decimal_places", elem_type="i32", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="parameter.keyform_binding_begin_indices", elem_type="i32", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="parameter.keyform_binding_counts", elem_type="i32", count_idx=5, align=64, group="parameter"),
    SectionEntry(name="part_keyform.draw_orders", elem_type="f32", count_idx=6, align=64, group="part_keyform"),
    SectionEntry(name="warp_deformer_keyform.opacities", elem_type="f32", count_idx=7, align=64, group="warp_deformer_keyform"),
    SectionEntry(name="warp_deformer_keyform.keyform_position_begin_indices", elem_type="i32", count_idx=7, align=64, group="warp_deformer_keyform"),
    SectionEntry(name="rotation_deformer_keyform.opacities", elem_type="f32", count_idx=8, align=64, group="rotation_deformer_keyform"),
    SectionEntry(name="rotation_deformer_keyform.angles", elem_type="f32", count_idx=8, align=64, group="rotation_deformer_keyform"),
    SectionEntry(name="rotation_deformer_keyform.origin_xs", elem_type="f32", count_idx=8, align=64, group="rotation_deformer_keyform"),
    SectionEntry(name="rotation_deformer_keyform.origin_ys", elem_type="f32", count_idx=8, align=64, group="rotation_deformer_keyform"),
    SectionEntry(name="rotation_deformer_keyform.scales", elem_type="f32", count_idx=8, align=64, group="rotation_deformer_keyform"),
    SectionEntry(name="rotation_deformer_keyform.reflect_xs", elem_type="bool", count_idx=8, align=64, group="rotation_deformer_keyform"),
    SectionEntry(name="rotation_deformer_keyform.reflect_ys", elem_type="bool", count_idx=8, align=64, group="rotation_deformer_keyform"),
    SectionEntry(name="art_mesh_keyform.opacities", elem_type="f32", count_idx=9, align=64, group="art_mesh_keyform"),
    SectionEntry(name="art_mesh_keyform.draw_orders", elem_type="f32", count_idx=9, align=64, group="art_mesh_keyform"),
    SectionEntry(name="art_mesh_keyform.keyform_position_begin_indices", elem_type="i32", count_idx=9, align=64, group="art_mesh_keyform"),
    SectionEntry(name="keyform_position.xys", elem_type="f32", count_idx=10, align=64, group="keyform_position"),
    SectionEntry(name="keyform_binding_index.indices", elem_type="i32", count_idx=11, align=64, group="keyform_binding_index"),
    SectionEntry(name="keyform_binding_band.begin_indices", elem_type="i32", count_idx=12, align=64, group="keyform_binding_band"),
    SectionEntry(name="keyform_binding_band.counts", elem_type="i32", count_idx=12, align=64, group="keyform_binding_band"),
    SectionEntry(name="keyform_binding.keys_begin_indices", elem_type="i32", count_idx=13, align=64, group="keyform_binding"),
    SectionEntry(name="keyform_binding.keys_counts", elem_type="i32", count_idx=13, align=64, group="keyform_binding"),
    SectionEntry(name="keys.values", elem_type="f32", count_idx=14, align=64, group="keys"),
    SectionEntry(name="uv.xys", elem_type="f32", count_idx=15, align=64, group="uv"),
    SectionEntry(name="position_index.indices", elem_type="i16", count_idx=16, align=64, group="position_index"),
    SectionEntry(name="drawable_mask.art_mesh_indices", elem_type="i32", count_idx=17, align=64, group="drawable_mask"),
    SectionEntry(name="draw_order_group.object_begin_indices", elem_type="i32", count_idx=18, align=64, group="draw_order_group"),
    SectionEntry(name="draw_order_group.object_counts", elem_type="i32", count_idx=18, align=64, group="draw_order_group"),
    SectionEntry(name="draw_order_group.object_total_counts", elem_type="i32", count_idx=18, align=64, group="draw_order_group"),
    SectionEntry(name="draw_order_group.min_draw_orders", elem_type="i32", count_idx=18, align=64, group="draw_order_group"),
    SectionEntry(name="draw_order_group.max_draw_orders", elem_type="i32", count_idx=18, align=64, group="draw_order_group"),
    SectionEntry(name="draw_order_group_object.types", elem_type="i32", count_idx=19, align=64, group="draw_order_group_object"),
    SectionEntry(name="draw_order_group_object.indices", elem_type="i32", count_idx=19, align=64, group="draw_order_group_object"),
    SectionEntry(name="draw_order_group_object.group_indices", elem_type="i32", count_idx=19, align=64, group="draw_order_group_object"),
    SectionEntry(name="glue.runtime_space", elem_type="runtime", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue.ids", elem_type="str64", count_idx=20, align=0, group="glue"),
    SectionEntry(name="glue.keyform_binding_band_indices", elem_type="i32", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue.keyform_begin_indices", elem_type="i32", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue.keyform_counts", elem_type="i32", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue.art_mesh_index_as", elem_type="i32", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue.art_mesh_index_bs", elem_type="i32", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue.info_begin_indices", elem_type="i32", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue.info_counts", elem_type="i32", count_idx=20, align=64, group="glue"),
    SectionEntry(name="glue_info.weights", elem_type="f32", count_idx=21, align=64, group="glue_info"),
    SectionEntry(name="glue_info.position_indices", elem_type="i16", count_idx=21, align=64, group="glue_info"),
    SectionEntry(name="glue_keyform.intensities", elem_type="f32", count_idx=22, align=64, group="glue_keyform"),
]


# 版本 >= V3_03 时追加的 section；SOT 槽位数随之增加，写出时必须计入。
ADDITIONAL_V303: List[SectionEntry] = [
    SectionEntry(name="additional.quad_transforms", elem_type="bool", count_idx=-1, align=0, group="additional"),
]

_SECTION_BY_NAME: Dict[str, SectionEntry] = {
    e.name: e for e in SECTION_LAYOUT + ADDITIONAL_V303
}


def get_section(name: str) -> SectionEntry:
    """按名称取 section 定义；不存在则抛 KeyError。"""
    return _SECTION_BY_NAME[name]


def build_layout(version: int) -> List[SectionEntry]:
    """按格式版本返回实际使用的 section 序列。"""
    if version >= MocVersion.V3_03:
        return list(SECTION_LAYOUT) + list(ADDITIONAL_V303)
    return list(SECTION_LAYOUT)
