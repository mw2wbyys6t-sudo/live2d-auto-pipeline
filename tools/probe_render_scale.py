#!/usr/bin/env python3
"""测定 屏幕像素/画布像素 的映射系数，并检验它是否随 ppu 变化。

小尺寸下不裁剪，bbox 可直接反推系数：
    预测屏幕半宽 = half * K,  K = 视口宽 / 画布单位宽 = VIEW_W / (canvas_w / ppu)
若实测 K 与 ppu 无关（≈ VIEW_W / canvas_w），说明框架把位置当**画布像素**、
按画布尺寸铺满视口；若 K ≈ VIEW_W*ppu/canvas_w，说明它把位置当**单位**。
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

WORK = ROOT / "Work" / "render-scale"
VIEW_W, VIEW_H = 400, 500
CANVAS = 512.0


def drawn_extent(half: float, ppu: float):
    verts, uvs, tris = square(0.0, 0.0, half)
    spec = RigSpec(meshes=[MeshSpec("ArtMesh1", verts, tris, uvs)],
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=CANVAS, canvas_height=CANVAS,
                   pixels_per_unit=ppu)
    tag = f"h{half:g}_p{ppu:g}"
    manifest = write_package(WORK / tag, compile_static_rig(spec).to_bytes(), tag)
    png = WORK / tag / f"{tag}.png"
    r = render_probe(str(manifest), width=VIEW_W, height=VIEW_H, png=str(png))
    if not r["ok"]:
        return None
    im = np.asarray(Image.open(png)).astype(int)
    mask = (im[:, :, 0] > 200) & (im[:, :, 2] > 200) & (im[:, :, 1] < 80)
    if not mask.any():
        return (0.0, 0.0)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return (float(cols.max() - cols.min() + 1), float(rows.max() - rows.min() + 1))


def main() -> int:
    for ppu in (100.0, 200.0):
        print(f"--- ppu={ppu:g} (画布单位宽 = {CANVAS / ppu:.3f}) ---")
        for half in (0.5, 1.0, 2.0):
            extent = drawn_extent(half, ppu)
            if extent is None:
                print("  渲染失败")
                continue
            w, h = extent
            kx = w / (2 * half) if half else 0.0
            ky = h / (2 * half) if half else 0.0
            print(f"  半边长 {half:>4} 画布px → 屏幕 {w:>5.0f}x{h:<5.0f} "
                  f"Kx={kx:6.2f} Ky={ky:6.2f}  "
                  f"(候选: VIEW/canvas={VIEW_W / CANVAS:.2f}, "
                  f"VIEW*ppu/canvas={VIEW_W * ppu / CANVAS:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
