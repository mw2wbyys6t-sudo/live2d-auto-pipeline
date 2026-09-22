#!/usr/bin/env python3
"""重试未覆盖的候选：让变形器带上真实参数绑定（生产目标形状）。

之前那轮 bisect 在跑到 2 关键形用例之前就崩了，等于从没测过它。
每个候选跑一次官方一致性约 1.5 秒。
运行：.venv/Scripts/python.exe tools/retry_deformer_candidates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import (  # noqa: E402
    verify_moc3_consistency, verify_moc3_load)
from live2d_builder.exporter.moc3_lint import lint_document  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid, KeyformShape, MeshSpec, ParameterSpec, RigSpec,
    WarpDeformerSpec, compile_static_rig)
from tools.moc3_probe_kit import write_package  # noqa: E402

OUT = ROOT / "Work" / "deformer-retry"
HALF, CANVAS, PPU = 100.0, 512.0, 100.0


def grid(shift: float):
    step = 2 * HALF / 2
    return [(x + shift, y) for row in range(3) for col in range(3)
            for x, y in [(-HALF + col * step, -HALF + row * step)]]


def build(deformer_grids=1, mesh_shapes=False, extra_part=False):
    verts = [(-HALF, -HALF), (HALF, -HALF), (HALF, HALF), (-HALF, HALF)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    tris = [(0, 1, 2), (0, 2, 3)]
    shapes = ()
    if mesh_shapes:
        shapes = tuple(KeyformShape(k, [(x + k * 0.5, y) for x, y in verts])
                       for k in (-30.0, 30.0))
    mesh = MeshSpec("ArtMesh1", verts, tris, uvs, deformer_id="Warp1",
                    keyform_parameter_id="ParamAngleX" if shapes else "",
                    keyform_shapes=shapes)
    keys = [-30.0, 30.0] if deformer_grids > 1 else [0.0]
    deformer = WarpDeformerSpec(
        deformer_id="Warp1", rows=2, cols=2,
        grids=[DeformerGrid(k, grid(0.0 if k < 0 else 20.0)) for k in keys],
        parameter_id="ParamAngleX" if deformer_grids > 1 else "",
        parent_part_id="Part_ArtMesh1")
    specs = [mesh]
    if extra_part:
        v2, u2, t2 = verts, uvs, tris
        specs.append(MeshSpec("ArtMesh2", [(x + 220, y) for x, y in v2], t2, u2,
                              part_id="Part_Warp1"))
    spec = RigSpec(meshes=specs,
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=CANVAS, canvas_height=CANVAS,
                   pixels_per_unit=PPU, deformers=[deformer], deformer_count=1)
    keep = RigSpec.validate
    RigSpec.validate = lambda self: None          # 仅诊断进程内绕过拒绝门
    try:
        return compile_static_rig(spec)
    finally:
        RigSpec.validate = keep


def run(tag: str, doc):
    issues = [str(i) for i in lint_document(doc)]
    path = OUT / f"{tag}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(path))
    note = "" if result["ok"] else f"  {str(result['blocker'])[:50]}  lint={issues[:2]}"
    print(f"  {tag:40s} 一致={result['ok']}{note}")
    if result["ok"]:
        manifest = write_package(OUT / tag, doc.to_bytes(), tag)
        loaded = verify_moc3_load(str(manifest))
        print(f"    加载 ok={loaded['ok']} 参数={loaded['parameter_ids']}")
    return result["ok"]


def main() -> int:
    print("V0 单关键形、空带（已知失败的对照）")
    run("v0_single_grid", build())
    print("V1 两关键形 + 真实绑定带 + 参数绑定（生产目标形状）")
    run("v1_bound_two_keys", build(deformer_grids=2))
    print("V2 在 V1 基础上，网格自己也带形状键形")
    run("v2_plus_mesh_shapes", build(deformer_grids=2, mesh_shapes=True))
    print("V3 在 V1 基础上，为变形器另建一个部件（Part_Warp1）")
    run("v3_deformer_part", build(deformer_grids=2, extra_part=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
