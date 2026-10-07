#!/usr/bin/env python3
"""扫参实验：固定贴图与网格形状，只改中心 / 半边长，量渲染落点，反推视口映射。

运行：.venv/Scripts/python.exe tools/probe_render_sweep.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.moc3_probe_kit import square, write_package  # noqa: E402
from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
)

WORK = ROOT / "Work" / "render-sweep"
PRIME = "--no-prime" not in sys.argv
VIEW = (400, 500)


def package(tag: str, cx: float, cy: float, half: float, ppu: float = 100.0):
    verts, uvs, tris = square(cx, cy, half)
    spec = RigSpec(meshes=[MeshSpec("ArtMesh1", verts, tris, uvs)],
                   parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                   canvas_width=512.0, canvas_height=512.0, pixels_per_unit=ppu)
    doc = compile_static_rig(spec)
    return write_package(WORK / tag, doc.to_bytes(), tag)


def measure(tag: str) -> tuple:
    import numpy as np
    from PIL import Image
    manifest = package(tag, *CASES[tag])
    r = render_probe(str(manifest), width=VIEW[0], height=VIEW[1],
                     png=str(WORK / tag / f"{tag}.png"), prime=PRIME)
    if not r["ok"]:
        return ("渲染失败", r["blocker"])
    im = np.asarray(Image.open(WORK / tag / f"{tag}.png")).astype(int)
    magenta = ((im[:, :, 0] > 200) & (im[:, :, 2] > 200) & (im[:, :, 1] < 80))
    n = int(magenta.sum())
    if not n:
        return ("无", 0)
    rows = np.flatnonzero(magenta.any(axis=1))
    cols = np.flatnonzero(magenta.any(axis=0))
    # 图像坐标（已翻转，0=顶部）
    return (f"x[{cols.min()}..{cols.max()}] y[{rows.min()}..{rows.max()}]", n)


CASES: dict[str, tuple] = {}


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"=== 参数预热: {'开' if PRIME else '关'}")
    print("=== 固定 half=10，平移中心（画布像素，原点在画布中心，y 向上）===")
    for cx, cy in [(0, 0), (50, 0), (100, 0), (150, 0), (0, 50), (0, 100), (0, 150),
                   (-150, 150)]:
        tag = f"c_{cx}_{cy}"
        CASES[tag] = (float(cx), float(cy), 10.0)
        bbox, n = measure(tag)
        print(f"  中心({cx:>4},{cy:>4}) → {bbox} 像素={n}")

    print("\n=== 固定中心 0，放大半边长 ===")
    for half in (2.0, 10.0, 50.0, 128.0, 256.0, 600.0):
        tag = f"h_{int(half)}"
        CASES[tag] = (0.0, 0.0, half)
        bbox, n = measure(tag)
        print(f"  半边长 {half:>5.0f} → {bbox} 像素={n}")

    print("\n=== 固定 half=10，改 pixels_per_unit ===")
    for ppu in (50.0, 100.0, 256.0, 512.0, 2400.0):
        tag = f"p_{int(ppu)}"
        CASES[tag] = (0.0, 0.0, 10.0)
        # 用 ppu 参数重建
        verts, uvs, tris = square(0.0, 0.0, 10.0)
        spec = RigSpec(meshes=[MeshSpec("ArtMesh1", verts, tris, uvs)],
                       parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                       canvas_width=512.0, canvas_height=512.0, pixels_per_unit=ppu)
        manifest = write_package(WORK / tag, compile_static_rig(spec).to_bytes(), tag)
        import numpy as np
        from PIL import Image
        r = render_probe(str(manifest), width=VIEW[0], height=VIEW[1],
                         png=str(WORK / tag / f"{tag}.png"), prime=PRIME)
        if not r["ok"]:
            print(f"  ppu={ppu:>6.0f} → 失败 {r['blocker']}")
            continue
        im = np.asarray(Image.open(WORK / tag / f"{tag}.png")).astype(int)
        magenta = ((im[:, :, 0] > 200) & (im[:, :, 2] > 200) & (im[:, :, 1] < 80))
        rows = np.flatnonzero(magenta.any(axis=1))
        cols = np.flatnonzero(magenta.any(axis=0))
        span = (f"x[{cols.min()}..{cols.max()}] y[{rows.min()}..{rows.max()}]"
                if cols.size else "无")
        print(f"  ppu={ppu:>6.0f} (半边长={10.0 / ppu:.4f} 单位) → {span} "
              f"像素={int(magenta.sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
