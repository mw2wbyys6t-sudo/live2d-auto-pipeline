#!/usr/bin/env python3
"""判定 pixels_per_unit 该取什么值：真实导出的可见性问题。

画布 64px、方块 ±24px（与语义图层同样的相对尺寸），只改 ppu，量渲染落点。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from tools.moc3_probe_kit import square, write_package  # noqa: E402
from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
)

WORK = ROOT / "Work" / "ppu-search"


def run(ppu: float, canvas: float, half: float):
    verts, uvs, tris = square(0.0, 0.0, half)
    spec = RigSpec(meshes=[MeshSpec("ArtMesh1", verts, tris, uvs)],
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=canvas, canvas_height=canvas,
                   pixels_per_unit=ppu)
    tag = f"ppu{ppu:g}"
    manifest = write_package(WORK / tag, compile_static_rig(spec).to_bytes(), tag)
    png = WORK / tag / f"{tag}.png"
    r = render_probe(str(manifest), width=400, height=500, png=str(png))
    if not r["ok"]:
        return f"失败 {r['blocker']}"
    if r["alpha_bbox"] is None:
        return "完全不可见（0 不透明像素）"
    im = np.asarray(Image.open(png)).astype(int)
    magenta = (im[:, :, 0] > 200) & (im[:, :, 2] > 200) & (im[:, :, 1] < 80)
    rows = np.flatnonzero(magenta.any(axis=1))
    cols = np.flatnonzero(magenta.any(axis=0))
    if not cols.size:
        return "有 alpha 但无色块"
    return (f"贴图色块 {cols.max()-cols.min()+1}x{rows.max()-rows.min()+1} "
            f"@({cols.min()},{rows.min()})")


def main() -> int:
    canvas, half = 64.0, 24.0     # 内容占画布 75%
    print(f"画布 {canvas:g}px，方块 ±{half:g}px（占画布 {2*half/canvas:.0%}）；"
          f"官方 Haru 的 ppu == canvas_width，内容约占画布 19%\n")
    for ppu in (64.0, 32.0, 16.0, 8.0, 4.0, 1.28, 1.0, 0.16, 100.0):
        print(f"  ppu={ppu:>7g} → {run(ppu, canvas, half)}")

    print("\n再测真实画布尺寸 1024（AI 出图常见），内容占 75%：")
    for ppu in (1024.0, 256.0, 512.0, 2.56, 100.0):
        print(f"  ppu={ppu:>7g} → {run(ppu, 1024.0, 384.0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
