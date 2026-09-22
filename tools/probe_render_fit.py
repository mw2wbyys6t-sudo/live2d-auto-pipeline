#!/usr/bin/env python3
"""判别渲染缩放规律：是按「±1 单位」固定映射，还是按模型包围盒拉伸适配。

用例：
  flat       单个扁平方块（x ±200, y ±20）。固定映射 → 细条；bbox 适配 → 铺满。
  twomesh    大框 + 小点。bbox 适配 → 小点相对大框保持比例。
运行：.venv/Scripts/python.exe tools/probe_render_fit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.moc3_probe_kit import write_package  # noqa: E402
from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
)

WORK = ROOT / "Work" / "render-fit"


def _rect(cx, cy, hx, hy):
    verts = [(cx - hx, cy - hy), (cx + hx, cy - hy),
             (cx + hx, cy + hy), (cx - hx, cy + hy)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return verts, uvs, [(0, 1, 2), (0, 2, 3)]


def _package(name, meshes):
    spec = RigSpec(meshes=meshes,
                  parameters=[ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)],
                  canvas_width=512.0, canvas_height=512.0,
                  pixels_per_unit=100.0)
    doc = compile_static_rig(spec)
    return write_package(WORK / name, doc.to_bytes(), name)


def _measure(label, manifest):
    r = render_probe(str(manifest), width=400, height=500,
                     png=str(WORK / label / f"{label}.png"))
    if not r["ok"]:
        print(f"{label:12s} 失败: {r['blocker']}")
        return None
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(WORK / label / f"{label}.png")).astype(int)
    flat = im.reshape(-1, 4)
    # 以出现最多的颜色作为背景，量「与背景不同」的区域
    background = np.unique(flat, axis=0, return_counts=True)
    modal = background[0][background[1].argmax()]
    mask = (np.abs(im - modal).max(axis=2) > 16)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if not cols.size or int(mask.sum()) > 0.98 * mask.size:
        print(f"{label:12s} 整帧同色或无色（主色 {tuple(modal)}，"
              f"差异像素 {int(mask.sum())}）→ 几何未影响画面")
        return None
    print(f"{label:12s} 着色 bbox=({cols.min()},{rows.min()})-({cols.max()},{rows.max()}) "
          f"w={cols.max()-cols.min()+1} h={rows.max()-rows.min()+1} 面积={int(mask.sum())}")
    return mask


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    print("清屏色与贴图色不同才能量出轮廓；贴图用不透明品红，清屏为透明黑。")

    v, u, t = _rect(0, 0, 200, 20)
    _measure("flat", _package("flat", [
        MeshSpec("ArtMeshFlat", v, t, u)]))

    # 大框 + 右上角小点：两点都在同一模型里，bbox 适配会同时缩放
    big_v, big_u, big_t = _rect(0, 0, 200, 200)
    dot_v, dot_u, dot_t = _rect(150, 150, 10, 10)
    _measure("twomesh", _package("twomesh", [
        MeshSpec("ArtMeshBig", big_v, big_t, big_u),
        MeshSpec("ArtMeshDot", dot_v, dot_t, dot_u, draw_order=1.0)]))

    # 单独只放小点：bbox 适配会把它放大到整屏，固定映射则仍是小点
    _measure("dot_only", _package("dot_only", [
        MeshSpec("ArtMeshDot", dot_v, dot_t, dot_u)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
