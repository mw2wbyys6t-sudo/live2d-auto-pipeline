#!/usr/bin/env python3
"""对含变形器的文档做单点变异，找出官方内核真正要求的那条规则。

编码保真度已被证明（Haru 经 py-moc3 读回再写出与原文件逐字节相同且仍过内核），
所以这里只改「数值/结构语义」。每个候选跑一次官方一致性约 1.5 秒。
运行：.venv/Scripts/python.exe tools/mutate_deformer_doc.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
    compile_static_rig,
)

OUT = ROOT / "Work" / "deformer-mutations"
HALF, CANVAS, PPU = 100.0, 512.0, 100.0


def base_doc():
    """构造带 warp 变形器的文档（绕过产品侧的显式拒绝，仅此处生效）。"""
    verts = [(-HALF, -HALF), (HALF, -HALF), (HALF, HALF), (-HALF, HALF)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    mesh = MeshSpec("ArtMesh1", verts, [(0, 1, 2), (0, 2, 3)], uvs,
                    deformer_id="Warp1")
    step = 2 * HALF / 2
    points = [(-HALF + col * step, -HALF + row * step)
              for row in range(3) for col in range(3)]
    deformer = WarpDeformerSpec(
        deformer_id="Warp1", rows=2, cols=2,
        grids=[DeformerGrid(0.0, points)],
        parent_part_id=mesh.effective_part_id)
    spec = RigSpec(meshes=[mesh],
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=CANVAS, canvas_height=CANVAS,
                   pixels_per_unit=PPU, deformers=[deformer], deformer_count=1)
    keep = RigSpec.validate
    RigSpec.validate = lambda self: None
    try:
        return compile_static_rig(spec), mesh, deformer
    finally:
        RigSpec.validate = keep


def aligned(length: int) -> int:
    return -(-length // 16) * 16


def variant_pool_order(doc, mesh, deformer):
    """位置池里把变形器控制网格放在网格关键形之前。"""
    mesh_flat = [v / PPU for xy in mesh.vertices for v in xy]
    grid_flat = [v / PPU for xy in deformer.grids[0].points for v in xy]
    positions = grid_flat + [0.0] * (aligned(len(grid_flat)) - len(grid_flat)) + mesh_flat
    doc.set("keyform_position.xys", positions)
    doc.set("warp_deformer_keyform.keyform_position_begin_indices", [0])
    doc.set("art_mesh_keyform.keyform_position_begin_indices",
            [aligned(len(grid_flat))])
    return doc


def variant_own_band(doc):
    """给变形器一个真实的单绑定带（band 1，1 个 binding，1 个键）。"""
    doc.set("deformer.keyform_binding_band_indices", [1])
    doc.set("warp_deformer.keyform_binding_band_indices", [1])
    doc.set("keyform_binding_band.begin_indices", [0, 1])
    doc.set("keyform_binding_band.counts", [0, 1])
    doc.set("keyform_binding_index.indices", [0])
    doc.set("keyform_binding.keys_begin_indices", [0])
    doc.set("keyform_binding.keys_counts", [1])
    doc.set("keys.values", [0.0])
    doc.counts[ms.CountIdx.KEYFORM_BINDING_BANDS] = 2
    doc.counts[ms.CountIdx.KEYFORM_BINDING_INDICES] = 1
    doc.counts[ms.CountIdx.KEYFORM_BINDINGS] = 1
    doc.counts[ms.CountIdx.KEYS] = 1
    return doc


def variant_grid_covers_nothing(doc):
    """控制网格点全 0（退化网格），检验是否要求非退化。"""
    doc.set("warp_deformer_keyform.keyform_position_begin_indices", [32])
    positions = list(doc.get("keyform_position.xys")) + [0.0] * 18
    doc.set("keyform_position.xys", positions)
    doc.counts[ms.CountIdx.KEYFORM_POSITIONS] = len(positions)
    return doc


def check(tag: str, doc) -> bool:
    path = OUT / f"{tag}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(path))
    note = "" if result["ok"] else f"  ({result['blocker']})"
    print(f"  {tag:34s} ok={result['ok']}{note}")
    return result["ok"]


def main() -> int:
    doc, mesh, deformer = base_doc()
    print("0. 原始构造（基准）")
    check("plain", doc)
    print("1. 位置池：变形器网格放在网格关键形之前")
    doc2, m2, d2 = base_doc()
    check("pool_order", variant_pool_order(doc2, m2, d2))
    print("2. 变形器使用真实单绑定带而非空带")
    doc3, _, _ = base_doc()
    check("own_band", variant_own_band(doc3))
    print("3. 控制网格退化（全 0）")
    doc4, _, _ = base_doc()
    check("degenerate", variant_grid_covers_nothing(doc4))
    print("4. 同时应用 1+2")
    doc5, m5, d5 = base_doc()
    check("pool_and_band", variant_own_band(variant_pool_order(doc5, m5, d5)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
