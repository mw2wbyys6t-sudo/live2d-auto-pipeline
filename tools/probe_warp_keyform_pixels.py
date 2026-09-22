#!/usr/bin/env python3
"""决定性实验：多键形 warp 的第二个控制网格到底有没有被内核采用。

`tools/verify_breath_end_to_end.py` 里呼吸（幅度 2%）不动画面。可能是
  (A) 接线错 —— 内核根本没用第二个控制网格；或
  (B) 幅度太小/不可见。
本脚本把第二个控制网格**猛移 +50 模型单位**（远超任何幅度问题）：
  * 画面**不变** -> 是 (A) 接线问题；
  * 画面**改变** -> 是 (B)，接线没问题。

直接手工构造 RigSpec（不经 pipeline），把变量压到最少。
运行：.venv/Scripts/python.exe tools/probe_warp_keyform_pixels.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_lint import lint_document  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    WarpDeformerSpec,
    compile_static_rig,
)

CANVAS = 512.0
PPU = 100.0
SHIFT = 50.0            # 模型单位；远超呼吸的 2% 幅度
OUT = ROOT / "Work" / "warp-keyform-pixels"
RECT = (-110.0, -165.0, 110.0, 165.0)


def _lattice(rect, shift_y):
    x0, y0, x1, y1 = rect
    return [[x, y + shift_y] for y in (y0, y1) for x in (x0, x1)]


def _rig():
    verts = [(-100.0, -150.0), (100.0, -150.0), (100.0, 150.0), (-100.0, 150.0)]
    mesh = MeshSpec(
        mesh_id="body", vertices=verts, triangles=[(0, 1, 2), (0, 2, 3)],
        uvs=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        deformer_id="W")
    deformer = WarpDeformerSpec(
        deformer_id="W", rows=1, cols=1, parameter_id="ParamBreath",
        grids=[DeformerGrid(0.0, _lattice(RECT, 0.0)),
               DeformerGrid(1.0, _lattice(RECT, SHIFT))])
    return RigSpec(meshes=[mesh], parameters=[
        ParameterSpec("ParamBreath", minimum=0.0, maximum=1.0, default=0.0)],
        canvas_width=CANVAS, canvas_height=CANVAS, pixels_per_unit=PPU,
        deformers=[deformer])


def main() -> int:
    from PIL import Image

    doc = compile_static_rig(_rig())
    issues = lint_document(doc)
    if issues:
        print("lint 失败:", [str(i) for i in issues])
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "w.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(OUT / "t.png")
    manifest = OUT / "w.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": "w"},
        "FileReferences": {"Moc": "w.moc3", "Textures": ["t.png"]},
    }), encoding="utf-8")
    print(f"lint 通过。warp keyform_counts="
          f"{doc.get('warp_deformer.keyform_counts')} "
          f"position_begin={doc.get('warp_deformer_keyform.keyform_position_begin_indices')}")

    a = render_probe(str(manifest), {"ParamBreath": 0.0})
    b = render_probe(str(manifest), {"ParamBreath": 1.0})
    if not (a.get("ok") and b.get("ok")):
        print("渲染失败:", a.get("blocker"), b.get("blocker"))
        return 1
    changed = a["pixels_sha256"] != b["pixels_sha256"]
    print(f"键 0: {a['opaque_pixels']} 像素 {a['pixels_sha256'][:12]}")
    print(f"键 1: {b['opaque_pixels']} 像素 {b['pixels_sha256'][:12]}")
    print(f"\n第二个控制网格猛移 {SHIFT} 后画面是否改变：{changed}")
    if not changed:
        print("=> 结论：内核**没有**采用第二个控制网格 -> 接线问题（与幅度无关）")
        return 1
    print("=> 结论：接线没问题（之前不动是幅度/可见性问题）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
