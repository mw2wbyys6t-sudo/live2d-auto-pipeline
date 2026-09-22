#!/usr/bin/env python3
"""受控定论：warp 变形器控制网格该用哪种坐标空间与点序。

原理：恒等控制网格（每个控制点正好落在它「应该」覆盖的位置上）对渲染必须是
**空操作**。因此把基准模型的像素哈希拿来逐一比对，能匹配上的那一组
（坐标空间 × 点序）就是官方格式的真实约定；全部不匹配则说明还有别的条件。

运行：.venv/Scripts/python.exe tools/probe_warp_grid_space.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.moc3_probe_kit import square, write_package  # noqa: E402
from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
    compile_static_rig,
)

WORK = ROOT / "Work" / "warp-grid-space"
CANVAS = 512.0
PPU = 100.0
HALF = 100.0          # 方块半边长（画布像素）
ROWS = COLS = 2       # 格子数 -> 3x3 控制点


def lattice(extent: float, ordered: str):
    """在 [-extent, extent] 上生成 3x3 控制点。"""
    step = 2 * extent / ROWS
    points = []
    if ordered.startswith("row"):
        outer, inner = ROWS + 1, COLS + 1
        def at(o, i):
            return (-extent + i * step, -extent + o * step)
    else:
        outer, inner = COLS + 1, ROWS + 1
        def at(o, i):
            return (-extent + o * step, -extent + i * step)
    for o in range(outer):
        for i in range(inner):
            points.append(at(o, i))
    if ordered.endswith("down"):
        # 同样的点集，行序反过来
        size = ROWS + 1
        chunks = [points[k * size:(k + 1) * size] for k in range(size)]
        points = [p for chunk in reversed(chunks) for p in chunk]
    return points


def render(tag: str, spec: RigSpec):
    folder = WORK / tag
    doc = compile_static_rig(spec)
    manifest = write_package(folder, doc.to_bytes(), "m")
    result = render_probe(str(manifest), png=str(folder / "frame.png"))
    print(f"  {tag:34s} 不透明={result['opaque_pixels']:>6} "
          f"bbox={result['alpha_bbox']} sha={result['pixels_sha256'][:12]} "
          f"{'' if result['ok'] else 'blocker=' + str(result['blocker'])}")
    return result


def base_spec(mesh_extra=None) -> RigSpec:
    verts, uvs, tris = square(0.0, 0.0, HALF)
    mesh = MeshSpec("ArtMesh1", verts, tris, uvs)
    return RigSpec(meshes=[mesh],
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=CANVAS, canvas_height=CANVAS,
                   pixels_per_unit=PPU)


def spec_with_deformer(extent: float, ordered: str) -> RigSpec:
    verts, uvs, tris = square(0.0, 0.0, HALF)
    base = base_spec()
    mesh = MeshSpec("ArtMesh1", verts, tris, uvs, deformer_id="Warp1")
    deformer = WarpDeformerSpec(
        deformer_id="Warp1", rows=ROWS, cols=COLS,
        grids=[DeformerGrid(0.0, lattice(extent, ordered))],
        parent_part_id=mesh.effective_part_id)
    return RigSpec(meshes=[mesh], parameters=list(base.parameters),
                   canvas_width=CANVAS, canvas_height=CANVAS,
                   pixels_per_unit=PPU, deformers=[deformer],
                   deformer_count=1)


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    print("基准（无变形器）：")
    baseline = render("baseline", base_spec())
    if not baseline["ok"]:
        print("基准渲染失败，无法继续")
        return 1

    print("\n恒等网格候选（匹配基准 sha 的那组即为正确约定）：")
    matches = []
    for label, extent in (("像素空间", HALF), ("单位空间", HALF / PPU)):
        for ordered in ("row_up", "row_down", "col_up", "col_down"):
            spec = spec_with_deformer(extent, ordered)
            result = render(f"{label}_{ordered}", spec)
            if result["ok"] and result["pixels_sha256"] == baseline["pixels_sha256"]:
                matches.append((label, ordered))
    print(f"\n与基准完全一致的约定：{matches or '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
