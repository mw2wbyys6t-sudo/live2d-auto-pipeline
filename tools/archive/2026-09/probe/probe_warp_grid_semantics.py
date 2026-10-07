#!/usr/bin/env python3
"""定恒等判据：哪种「控制网格取值/行列解释」让挂在 warp 变形器下的网格与基线逐像素相同。

一致性 =True 之后剩下的问题只有「网格数组到底怎么解释」。恒等网格必须画面不变，
这是最锋利的判据：只要 sha 与无变形器基线相同，就证明该解释与内核一致。
运行：.venv/Scripts/python.exe tools/probe_warp_grid_semantics.py
"""
from __future__ import annotations

import itertools
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

OUT = ROOT / "Work" / "warp-semantics"
PPU = 100.0                      # base_doc 的 pixels_per_unit


def grid_rows(n: int, half: float, y_up: bool = True):
    """n x n 网格顶点，x 先变化（行优先）；half 为半边长。"""
    step = 2 * half / (n - 1)
    pts = []
    for r in range(n):
        for c in range(n):
            y = -half + r * step
            pts.append((c * step - half, y if y_up else -y))
    return pts


def render_sha(tag: str, doc):
    path = OUT / f"{tag}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    if not verify_moc3_consistency(str(path))["ok"]:
        return "一致性 False"
    manifest = write_package(OUT / tag, doc.to_bytes(), tag)
    result = render_probe(str(manifest), {PARAM: 0.0})
    if not result["ok"]:
        return f"渲染失败 {result['blocker']}"
    return f"sha={result['pixels_sha256'][:12]} px={result['opaque_pixels']} " \
           f"bbox={result['alpha_bbox']}"


def main() -> int:
    base = base_doc()
    base_path = OUT / "baseline"
    base_path.mkdir(parents=True, exist_ok=True)
    (base_path / "b.moc3").write_bytes(base.to_bytes())
    manifest = write_package(base_path, base.to_bytes(), "b")
    baseline = render_probe(str(manifest), {PARAM: 0.0})
    want = baseline["pixels_sha256"][:12]
    print(f"基线 sha={want} px={baseline['opaque_pixels']} "
          f"bbox={baseline['alpha_bbox']}\n")

    print("== 网格半边长（单位 vs 像素）x 顶点行列数 ==")
    for n, half in itertools.product((3, 4), (1.0, 2.0, 4.0, PPU, 2 * PPU)):
        cells = n - 1
        doc = warp(base_doc(), [grid_rows(n, half)], band_index=0)
        doc.set("warp_deformer.vertex_counts", [n * n])
        doc.set("warp_deformer.rows", [cells])
        doc.set("warp_deformer.cols", [cells])
        doc.set("additional.quad_transforms", [0])
        got = render_sha(f"n{n}_h{int(half)}", doc)
        hit = "   <== 与基线逐像素相同" if str(want) in str(got) else ""
        print(f"  顶点 {n}x{n} 半边 {half:6.1f}  -> {got}{hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
