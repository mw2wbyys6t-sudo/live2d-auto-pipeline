#!/usr/bin/env python3
"""字段级二分：真实导出的 moc3 里，是哪一部分让官方运行时什么都不画？

每次都从管线产物重建 RigSpec，只改一个维度，然后渲染计数不透明像素。
运行：.venv/Scripts/python.exe tools/bisect_moc3_fields.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from drivers.live2d_runtime.moc3_verify import render_probe  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
)

SRC = ROOT / "Work" / "go-export-check" / "export"
WORK = ROOT / "Work" / "moc3-field-bisect"


def real_meshes():
    """从管线导出的 meshes.json 读回真实网格（画布像素、原点在中心）。"""
    data = json.loads((SRC / "gptest.meshes.json").read_text(encoding="utf-8"))
    specs = []
    for order, (name, mesh) in enumerate(data.items()):
        width, height = float(mesh["width"]), float(mesh["height"])
        verts = [[x - width / 2.0, height / 2.0 - y] for x, y in mesh["vertices"]]
        normals = mesh.get("vertices_norm")
        uvs = []
        for i, (x, y) in enumerate(mesh["vertices"]):
            if normals:
                nx, ny = float(normals[i][0]), float(normals[i][1])
            else:
                nx, ny = x / width, y / height
            uvs.append([nx, 1.0 - ny])
        tris = [[int(a), int(b), int(c)] for a, b, c in mesh["indices"]]
        specs.append((name, verts, tris, uvs, width, height))
    return specs


def build_and_render(tag: str, specs, canvas, params, uv_override=None,
                     reverse_winding=False):
    meshes = []
    for order, (name, verts, tris, uvs, w, h) in enumerate(specs):
        if uv_override is not None:
            uvs = uv_override(len(uvs))
        if reverse_winding:
            # y 轴翻转会把三角形的手性反过来；若运行时开启背面剔除，
            # 整个网格就会被丢掉。
            tris = [[a, c, b] for a, b, c in tris]
        meshes.append(MeshSpec(name, verts, tris, uvs, draw_order=float(order)))
    spec = RigSpec(meshes=meshes, parameters=params,
                   canvas_width=canvas[0], canvas_height=canvas[1],
                   pixels_per_unit=100.0)
    doc = compile_static_rig(spec)
    folder = WORK / tag
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / "m.moc3").write_bytes(doc.to_bytes())
    shutil.copy2(SRC / "gptest.texture_00.png", folder / "texture_00.png")
    manifest = folder / "m.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "FileReferences": {"Moc": "m.moc3", "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    r = render_probe(str(manifest))
    print(f"  {tag:28s} 不透明={r['opaque_pixels']:>6} bbox={r['alpha_bbox']} "
          f"{'' if r['ok'] else 'blocker=' + str(r['blocker'])}")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    specs = real_meshes()
    canvas = (specs[0][4], specs[0][5])
    print(f"真实网格 {len(specs)} 个，画布 {canvas}，每网格顶点数 "
          f"{[len(s[1]) for s in specs]}")
    many = [ParameterSpec("ParamAngleX", -30.0, 30.0, 0.0)]
    one_square = [(s[0], [[-24, -24], [24, -24], [24, 24], [-24, 24]],
                   [(0, 1, 2), (0, 2, 3)],
                   [[0, 0], [1, 0], [1, 1], [0, 1]], *canvas) for s in specs]

    build_and_render("A_real_1param", specs, canvas, many)
    build_and_render("B_real_28param", specs, canvas,
                     [ParameterSpec(f"P{i}", -1.0, 1.0, 0.0) for i in range(28)])
    build_and_render("C_only_first_mesh", specs[:1], canvas, many)
    build_and_render("D_uv_full_quad", specs, canvas, many,
                     uv_override=lambda n: [[i / max(1, n - 1), 0.5] for i in range(n)])
    build_and_render("E_squares_3mesh", one_square, canvas, many)
    build_and_render("F_square_1mesh", one_square[:1], canvas, many)
    build_and_render("G_real_reversed_winding", specs, canvas, many,
                     reverse_winding=True)
    build_and_render("H_real_reversed_1mesh", specs[:1], canvas, many,
                     reverse_winding=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
