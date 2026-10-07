#!/usr/bin/env python3
"""从合法文档「逐级长出」变形器，找出内核从哪一级开始翻脸。

每一级只加一件事，跑一次官方一致性。第一个失败的级别就是缺失约束的所在。
运行：.venv/Scripts/python.exe tools/ladder_deformer_growth.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402
from live2d_builder.exporter.moc3_container import CanvasInfo, Moc3Container  # noqa: E402
from live2d_builder.exporter.moc3_lint import lint_document  # noqa: E402

OUT = ROOT / "Work" / "ladder"
CI = ms.CountIdx


def base_doc() -> Moc3Container:
    """一个已知合法的极简文档：1 部件 + 1 网格 + 1 参数，无变形器。"""
    doc = Moc3Container(version=ms.MocVersion.V3_03)
    doc.canvas = CanvasInfo(pixels_per_unit=100.0, origin_x=256.0, origin_y=256.0,
                            canvas_width=512.0, canvas_height=512.0)
    c = doc.counts
    c[CI.PARTS] = 1
    c[CI.ART_MESHES] = 1
    c[CI.PART_KEYFORMS] = 1
    c[CI.ART_MESH_KEYFORMS] = 1
    c[CI.KEYFORM_POSITIONS] = 8
    c[CI.UVS] = 8
    c[CI.POSITION_INDICES] = 6
    c[CI.PARAMETERS] = 1
    c[CI.KEYFORM_BINDING_BANDS] = 1
    c[CI.DRAW_ORDER_GROUPS] = 1
    c[CI.DRAW_ORDER_GROUP_OBJECTS] = 1

    doc.set("part.ids", ["Part1"])
    doc.set("part.keyform_binding_band_indices", [0])
    doc.set("part.keyform_begin_indices", [0])
    doc.set("part.keyform_counts", [1])
    doc.set("part.visibles", [1])
    doc.set("part.enables", [1])
    doc.set("part.parent_part_indices", [-1])
    doc.set("part_keyform.draw_orders", [0.0])

    doc.set("art_mesh.ids", ["ArtMesh1"])
    doc.set("art_mesh.keyform_binding_band_indices", [0])
    doc.set("art_mesh.keyform_begin_indices", [0])
    doc.set("art_mesh.keyform_counts", [1])
    doc.set("art_mesh.visibles", [1])
    doc.set("art_mesh.enables", [1])
    doc.set("art_mesh.parent_part_indices", [0])
    doc.set("art_mesh.parent_deformer_indices", [-1])
    doc.set("art_mesh.texture_indices", [0])
    doc.set("art_mesh.drawable_flags", [0])
    doc.set("art_mesh.position_index_counts", [4])
    doc.set("art_mesh.uv_begin_indices", [0])
    doc.set("art_mesh.position_index_begin_indices", [0])
    doc.set("art_mesh.vertex_counts", [6])
    doc.set("art_mesh.mask_begin_indices", [0])
    doc.set("art_mesh.mask_counts", [0])

    doc.set("uv.xys", [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0])
    doc.set("position_index.indices", [0, 1, 2, 0, 2, 3])
    doc.set("keyform_position.xys", [-1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0])
    doc.set("art_mesh_keyform.opacities", [1.0])
    doc.set("art_mesh_keyform.draw_orders", [0.0])
    doc.set("art_mesh_keyform.keyform_position_begin_indices", [0])

    doc.set("keyform_binding_band.begin_indices", [0])
    doc.set("keyform_binding_band.counts", [0])
    doc.set("keyform_binding_index.indices", [])
    doc.set("parameter.ids", ["ParamAngleZ"])
    doc.set("parameter.min_values", [-30.0])
    doc.set("parameter.max_values", [30.0])
    doc.set("parameter.default_values", [0.0])
    doc.set("parameter.repeats", [0])
    doc.set("parameter.decimal_places", [2])
    doc.set("parameter.keyform_binding_begin_indices", [0])
    doc.set("parameter.keyform_binding_counts", [0])
    doc.set("draw_order_group.object_begin_indices", [0])
    doc.set("draw_order_group.object_counts", [1])
    doc.set("draw_order_group.object_total_counts", [1])
    doc.set("draw_order_group.min_draw_orders", [0])
    doc.set("draw_order_group.max_draw_orders", [0])
    doc.set("draw_order_group_object.types", [0])
    doc.set("draw_order_group_object.indices", [0])
    doc.set("draw_order_group_object.group_indices", [-1])
    return doc


GRID = [-1.0, -1.0, 0.0, -1.0, 1.0, -1.0,
        -1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
        -1.0, 1.0, 0.0, 1.0, 1.0, 1.0]


def add_deformer_table(doc):
    doc.counts[CI.DEFORMERS] = 1
    doc.set("deformer.ids", ["Warp1"])
    doc.set("deformer.keyform_binding_band_indices", [0])
    doc.set("deformer.visibles", [1])
    doc.set("deformer.enables", [1])
    doc.set("deformer.parent_part_indices", [0])
    doc.set("deformer.parent_deformer_indices", [-1])
    doc.set("deformer.types", [0])
    doc.set("deformer.specific_indices", [0])


def add_warp_table(doc):
    doc.counts[CI.WARP_DEFORMERS] = 1
    doc.set("warp_deformer.keyform_binding_band_indices", [0])
    doc.set("warp_deformer.keyform_begin_indices", [0])
    doc.set("warp_deformer.keyform_counts", [1])
    doc.set("warp_deformer.vertex_counts", [9])
    doc.set("warp_deformer.rows", [2])
    doc.set("warp_deformer.cols", [2])


def add_warp_keyforms(doc):
    doc.counts[CI.WARP_DEFORMER_KEYFORMS] = 1
    doc.set("warp_deformer_keyform.opacities", [1.0])
    doc.set("warp_deformer_keyform.keyform_position_begin_indices", [16])
    positions = list(doc.get("keyform_position.xys"))
    positions += [0.0] * (16 - len(positions))     # 补齐到 16
    positions += GRID
    doc.set("keyform_position.xys", positions)
    doc.counts[CI.KEYFORM_POSITIONS] = len(positions)


def link_mesh(doc):
    doc.set("art_mesh.parent_deformer_indices", [0])


STEPS = [
    ("L0 无变形器（合法控制组)", lambda d: None),
    ("L1 + deformer 公共表", add_deformer_table),
    ("L2 + warp 子表", add_warp_table),
    ("L3 + warp 关键形与位置池", add_warp_keyforms),
    ("L4 + 网格挂到变形器", link_mesh),
]


def run(doc, tag: str) -> bool:
    path = OUT / f"{tag.split()[0]}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(path))
    issues = [str(i) for i in lint_document(doc)]
    print(f"  {tag:34s} 一致={str(result['ok']):5s} lint={issues[:1] or '干净'}")
    if not result["ok"]:
        for line in (result["stdout"] + "\n" + result["stderr"]).strip().splitlines():
            print(f"      core: {line[:160]}")
        print(f"      blocker={result['blocker']}")
    return result["ok"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # 每一级都从干净的 base_doc 重新长：中途失败不能掩盖后面几级的信息。
    for i, (tag, apply) in enumerate(STEPS):
        doc = base_doc()
        for _, prev in STEPS[:i + 1]:
            prev(doc)
        run(doc, tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
