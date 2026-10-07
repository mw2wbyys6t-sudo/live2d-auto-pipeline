#!/usr/bin/env python3
"""二分：带 warp 变形器的文档被内核判为 invalid，问题在表本身还是在引用？

运行：.venv/Scripts/python.exe tools/diagnose_deformer_bisect.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.probe_warp_grid_space import lattice, spec_with_deformer  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
    compile_static_rig,
)
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402

CANVAS, PPU, HALF = 512.0, 100.0, 100.0
OUT = ROOT / "Work" / "diag"


def check(tag: str, spec: RigSpec) -> bool:
    doc = compile_static_rig(spec)
    path = OUT / f"{tag}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(path))
    print(f"  {tag:36s} ok={result['ok']} {result['blocker'] or ''}")
    return result["ok"]


def rig(link: bool, grids=1, rows=2, cols=2, extent=HALF):
    base = spec_with_deformer(extent, "row_up") if link else None
    verts, uvs, tris = [(-HALF, -HALF), (HALF, -HALF), (HALF, HALF), (-HALF, HALF)], \
        [(0, 0), (1, 0), (1, 1), (0, 1)], [(0, 1, 2), (0, 2, 3)]
    mesh = MeshSpec("ArtMesh1", verts, tris, uvs, deformer_id="Warp1" if link else "")
    keys = [0.0] if grids == 1 else [-30.0, 30.0]
    deformer = WarpDeformerSpec(
        deformer_id="Warp1", rows=rows, cols=cols,
        grids=[DeformerGrid(k, lattice(extent, "row_up")) for k in keys],
        parameter_id="" if grids == 1 else "ParamAngleX",
        parent_part_id=mesh.effective_part_id)
    return RigSpec(meshes=[mesh],
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=CANVAS, canvas_height=CANVAS,
                   pixels_per_unit=PPU, deformers=[deformer], deformer_count=1)


def main() -> int:
    print("A. 有变形器表，但网格不引用它（parent_deformer_index = -1）")
    check("unlinked", rig(link=False))
    print("B. 网格引用变形器")
    check("linked", rig(link=True))
    print("C. 变形器 1x1 格子（4 个控制点）")
    check("linked_1x1", rig(link=True, rows=1, cols=1))
    print("D. 两个键形（带参数绑定）")
    check("linked_2keys", rig(link=True, grids=2))
    print("E. 引用但键形不匹配（rows/cols 与点数不符会被拒，这里改成 4x4 大网格）")
    check("linked_4x4", rig(link=True, rows=4, cols=4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
