"""绑定数据 -> moc3 section 图。纯函数，无 IO。

最小模型的自洽性要求（设计文档 C3-C6）：
  * 每个参数有完整绑定链：keyform_binding_* -> band -> index -> keys
  * 每个部件有 part_keyform.draw_orders
  * 每个网格有 art_mesh_keyform.*
  * keyform_position.xys 覆盖所有关键形顶点
  * draw_order_group* 覆盖全部 art mesh
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_container import CanvasInfo, Moc3Container

# 单位四边形（局部坐标，逆时针）
_QUAD: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
_QUAD_UV: List[float] = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
_QUAD_INDICES: List[int] = [0, 1, 2, 0, 2, 3]

_PARAM_MIN = -30.0
_PARAM_MAX = 30.0
_PARAM_KEYS = 3  # 最小：min / default / max 三个键


@dataclass(frozen=True)
class MinimalModelSpec:
    """最小模型参数。"""
    part_id: str = "Part1"
    art_mesh_id: str = "ArtMesh1"
    parameter_id: str = "ParamAngleX"
    width: float = 512.0
    height: float = 512.0


def build_minimal_model(spec: MinimalModelSpec) -> Moc3Container:
    """构建一个内在自洽的最小模型（1 部件 / 1 网格 / 1 参数）。"""
    doc = Moc3Container(version=ms.MocVersion.V3_03)
    doc.canvas = CanvasInfo(
        pixels_per_unit=1.0,
        origin_x=0.0,
        origin_y=0.0,
        canvas_width=spec.width,
        canvas_height=spec.height,
    )

    _set_counts(doc)

    _write_part(doc, spec)
    _write_art_mesh(doc, spec)
    _write_parameter(doc, spec)
    _write_keys_and_bindings(doc, spec)
    _write_keyforms(doc, spec)
    _write_draw_order(doc, spec)
    return doc


def _set_counts(doc: Moc3Container) -> None:
    c = doc.counts
    c[ms.CountIdx.PARTS] = 1
    c[ms.CountIdx.DEFORMERS] = 0
    c[ms.CountIdx.WARP_DEFORMERS] = 0
    c[ms.CountIdx.ROTATION_DEFORMERS] = 0
    c[ms.CountIdx.ART_MESHES] = 1
    c[ms.CountIdx.PARAMETERS] = 1
    c[ms.CountIdx.PART_KEYFORMS] = 1
    # keyform 数必须等于带内各 binding 键数之积（对官方 Haru 全量核实）：
    # 1 个 binding x 3 个键 -> 3 个关键形
    c[ms.CountIdx.ART_MESH_KEYFORMS] = _PARAM_KEYS
    c[ms.CountIdx.WARP_DEFORMER_KEYFORMS] = 0
    c[ms.CountIdx.ROTATION_DEFORMER_KEYFORMS] = 0
    # 每个关键形 2x4 个浮点数，补齐到 16 个浮点数的倍数 -> 16 x 3
    c[ms.CountIdx.KEYFORM_POSITIONS] = 16 * _PARAM_KEYS
    c[ms.CountIdx.KEYFORM_BINDING_INDICES] = 1
    c[ms.CountIdx.KEYFORM_BINDING_BANDS] = 2
    c[ms.CountIdx.KEYFORM_BINDINGS] = 1
    c[ms.CountIdx.KEYS] = _PARAM_KEYS
    c[ms.CountIdx.UVS] = len(_QUAD_UV)                     # 浮点数个数
    c[ms.CountIdx.POSITION_INDICES] = len(_QUAD_INDICES)
    c[ms.CountIdx.DRAWABLE_MASKS] = 0
    c[ms.CountIdx.DRAW_ORDER_GROUPS] = 1
    c[ms.CountIdx.DRAW_ORDER_GROUP_OBJECTS] = 1
    c[ms.CountIdx.GLUES] = 0
    c[ms.CountIdx.GLUE_INFOS] = 0
    c[ms.CountIdx.GLUE_KEYFORMS] = 0


def _write_part(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("part.ids", [spec.part_id])
    # band 0 是官方约定的「空带」：所有不被参数驱动的部件都指向它
    doc.set("part.keyform_binding_band_indices", [0])
    doc.set("part.keyform_begin_indices", [0])
    doc.set("part.keyform_counts", [1])
    doc.set("part.visibles", [1])
    doc.set("part.enables", [1])
    doc.set("part.parent_part_indices", [-1])


def _write_art_mesh(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("art_mesh.ids", [spec.art_mesh_id])
    doc.set("art_mesh.keyform_binding_band_indices", [1])
    doc.set("art_mesh.keyform_begin_indices", [0])
    doc.set("art_mesh.keyform_counts", [_PARAM_KEYS])
    doc.set("art_mesh.visibles", [1])
    doc.set("art_mesh.enables", [1])
    doc.set("art_mesh.parent_part_indices", [0])
    doc.set("art_mesh.parent_deformer_indices", [-1])
    doc.set("art_mesh.texture_indices", [0])
    doc.set("art_mesh.drawable_flags", [0])
    # 字段名与直觉相反，已按官方 Haru 差分核实：
    #   position_index_counts = 唯一顶点数（决定 uv 段占用 2*该值 个浮点数）
    #   vertex_counts         = 索引条目数（决定 position_index 段占用该值 个索引）
    doc.set("art_mesh.position_index_counts", [len(_QUAD)])
    doc.set("art_mesh.uv_begin_indices", [0])
    doc.set("art_mesh.position_index_begin_indices", [0])
    doc.set("art_mesh.vertex_counts", [len(_QUAD_INDICES)])
    doc.set("art_mesh.mask_begin_indices", [0])
    doc.set("art_mesh.mask_counts", [0])

    doc.set("uv.xys", list(_QUAD_UV))
    doc.set("position_index.indices", list(_QUAD_INDICES))


def _write_parameter(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("parameter.ids", [spec.parameter_id])
    doc.set("parameter.min_values", [_PARAM_MIN])
    doc.set("parameter.max_values", [_PARAM_MAX])
    doc.set("parameter.default_values", [0.0])
    doc.set("parameter.repeats", [0])
    doc.set("parameter.decimal_places", [2])
    doc.set("parameter.keyform_binding_begin_indices", [0])
    doc.set("parameter.keyform_binding_counts", [1])


def _write_keys_and_bindings(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    # keys.values: 参数在 min / default / max 三处的取值
    doc.set("keys.values", [_PARAM_MIN, 0.0, _PARAM_MAX])

    # 绑定链（对官方 Haru 实测确认的引用关系）：
    #   对象.band_index -> band{begin,count} -> keyform_binding_index
    #     -> keyform_binding{keys_begin,keys_count} -> keys.values
    # band.counts 计的是「该带内绑定索引的条数」，不是键数。
    # band 0 是空带（count=0），供不被参数驱动的对象引用；官方数据中不存在 -1 带索引。
    doc.set("keyform_binding_band.begin_indices", [0, 0])
    doc.set("keyform_binding_band.counts", [0, 1])

    # keyform_binding_index.indices: 带内的绑定编号
    doc.set("keyform_binding_index.indices", [0])

    # keyform_binding: 每个绑定的键起始与个数
    doc.set("keyform_binding.keys_begin_indices", [0])
    doc.set("keyform_binding.keys_counts", [_PARAM_KEYS])


def _write_keyforms(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    # 部件关键形：绘制顺序
    doc.set("part_keyform.draw_orders", [0.0])

    # 网格关键形：不透明度 / 绘制顺序 / 顶点位置起始
    # 3 个键 -> 3 个关键形（乘积规则）
    doc.set("art_mesh_keyform.opacities", [1.0] * _PARAM_KEYS)
    doc.set("art_mesh_keyform.draw_orders", [0.0] * _PARAM_KEYS)
    # 每个关键形的位置起点对齐到 16 个浮点数（64 字节，官方导出器行为）
    doc.set("art_mesh_keyform.keyform_position_begin_indices",
            [16 * k for k in range(_PARAM_KEYS)])

    # 关键形顶点位置：单位四边形重复 3 份（最小模型不携带真实形变，
    # 形变证据由 moc3_model 的键型编译路径 + 像素验证提供）
    flat: List[float] = []
    for x, y in _QUAD:
        flat.extend((x, y))
    padded = flat + [0.0] * (16 - len(flat))
    doc.set("keyform_position.xys", padded * _PARAM_KEYS)


def _write_draw_order(doc: Moc3Container, spec: MinimalModelSpec) -> None:
    doc.set("draw_order_group.object_begin_indices", [0])
    doc.set("draw_order_group.object_counts", [1])
    doc.set("draw_order_group.object_total_counts", [1])
    doc.set("draw_order_group.min_draw_orders", [0])
    doc.set("draw_order_group.max_draw_orders", [0])

    # 对象类型：art mesh 用类型 0 表示（与官方 Haru 数据一致）
    doc.set("draw_order_group_object.types", [0])
    doc.set("draw_order_group_object.indices", [0])
    # 不参与动态绘制顺序分组的对象，组索引为 -1（官方即如此）
    doc.set("draw_order_group_object.group_indices", [-1])
