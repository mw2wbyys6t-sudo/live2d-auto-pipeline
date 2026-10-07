#!/usr/bin/env python3
"""实测：挂在 warp 变形器下的网格，其顶点必须写成网格内的 (s, t) 归一化坐标。

差分官方 Haru 得到：warp 下 80 个网格的顶点全部在 ~[0,1]（越界点略超出），
而 rotation 下 4 个网格的顶点在像素模型空间 [-230,233]；warp 网格自身也在像素空间。
所以内核做的是「顶点 (s,t) -> 双线性插值网格」。恒等判据：
网格取矩形、顶点按该矩形换算 (s,t)，画面必须与无变形器基线逐像素相同。
运行：.venv/Scripts/python.exe tools/probe_warp_st_identity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from drivers.live2d_runtime.moc3_verify import (  # noqa: E402
    render_probe, verify_moc3_consistency)
from ladder_deformer_growth import base_doc  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402
from moc3_probe_kit import write_package  # noqa: E402
from probe_warp_runtime_acceptance import PARAM, warp  # noqa: E402

OUT = ROOT / "Work" / "warp-st"
CI = ms.CountIdx
HALF = 1.0                 # base_doc 的方形半径（模型空间）


def rect_grid(n=3, x0=-HALF, x1=HALF, y0=-HALF, y1=HALF):
    step_x = (x1 - x0) / (n - 1)
    step_y = (y1 - y0) / (n - 1)
    return [(x0 + c * step_x, y0 + r * step_y)
            for r in range(n) for c in range(n)]


def to_st(verts, x0=-HALF, x1=HALF, y0=-HALF, y1=HALF):
    return [((x - x0) / (x1 - x0), (y - y0) / (y1 - y0)) for x, y in verts]


def square_verts():
    return [(-HALF, -HALF), (HALF, -HALF), (HALF, HALF), (-HALF, HALF)]


def set_mesh_positions(doc, verts):
    positions = list(doc.get("keyform_position.xys"))
    positions[:2 * len(verts)] = [float(v) for xy in verts for v in xy]
    doc.set("keyform_position.xys", positions)
    return doc


def shoot(tag: str, doc):
    path = OUT / f"{tag}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    consistency = verify_moc3_consistency(str(path))
    if not consistency["ok"]:
        return f"一致性 False ({consistency['blocker']})"
    manifest = write_package(OUT / tag, doc.to_bytes(), tag)
    result = render_probe(str(manifest), {PARAM: 0.0},
                          png=str(OUT / f"{tag}.png"))
    if not result["ok"]:
        return f"渲染失败 ({result['blocker']})"
    return (result["pixels_sha256"][:12], result["opaque_pixels"],
            result["alpha_bbox"])


def main() -> int:
    print("== 基线：无变形器，顶点在模型空间 ==")
    want = shoot("baseline", base_doc())
    print(f"  {want}")

    print("\n== 预测 A：顶点写 (s,t) + 矩形网格 -> 应与基线相同 ==")
    doc = warp(base_doc(), [rect_grid(3)])
    set_mesh_positions(doc, to_st(square_verts()))
    got = shoot("st_identity", doc)
    print(f"  {got}")
    same = isinstance(want, tuple) and isinstance(got, tuple) and got[0] == want[0]
    print(f"  ==> 恒等判据 {'通过' if same else '不通过'}")

    print("\n== 负对照 B：顶点仍写模型空间（应变形，即上面已观察到的错误画面）==")
    doc_b = warp(base_doc(), [rect_grid(3)])
    print(f"  {shoot('model_space', doc_b)}")

    print("\n== 预测 C：网格右移 0.5 单位 -> 画面应右移 ==")
    shifted = [(x + 0.5, y) for x, y in rect_grid(3)]
    doc_c = warp(base_doc(), [shifted])
    set_mesh_positions(doc_c, to_st(square_verts()))
    path = OUT / "shifted"
    manifest = write_package(path, doc_c.to_bytes(), "shifted")
    r = render_probe(str(manifest), {PARAM: 0.0}, png=str(OUT / "shifted.png"))
    print(f"  bbox={r['alpha_bbox']} px={r['opaque_pixels']} "
          f"质心={r['centroid']} sha={r['pixels_sha256'][:12]}")
    if isinstance(want, tuple) and r["ok"]:
        print(f"  基线质心={want[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
