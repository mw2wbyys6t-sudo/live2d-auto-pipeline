#!/usr/bin/env python3
"""变形器真实运行时验收：恒等网格必须逐像素不变，参数驱动必须改画面。

一致性 =True 只说明字节合法；这两条才是「变形器真的在工作」的证据。
运行：.venv/Scripts/python.exe tools/probe_warp_runtime_acceptance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from drivers.live2d_runtime.moc3_verify import (  # noqa: E402
    render_probe, verify_moc3_consistency, verify_moc3_load)
from ladder_deformer_growth import GRID, base_doc  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402
from moc3_probe_kit import write_package  # noqa: E402

OUT = ROOT / "Work" / "warp-runtime"
CI = ms.CountIdx
PARAM = "ParamAngleZ"
KEYS = (-30.0, 30.0)
SHIFT = 0.5           # 单位：网格第二形整体右移 0.5 单位


def warp(doc, grids, band_index=0):
    """给文档加 1 个 warp 变形器，grids 为每个关键形的 9 个 (x, y) 顶点。"""
    doc.counts[CI.DEFORMERS] = 1
    doc.counts[CI.WARP_DEFORMERS] = 1
    doc.counts[CI.WARP_DEFORMER_KEYFORMS] = len(grids)
    doc.set("deformer.ids", ["Warp1"])
    doc.set("deformer.keyform_binding_band_indices", [band_index])
    doc.set("deformer.visibles", [1])
    doc.set("deformer.enables", [1])
    doc.set("deformer.parent_part_indices", [0])
    doc.set("deformer.parent_deformer_indices", [-1])
    doc.set("deformer.types", [0])
    doc.set("deformer.specific_indices", [0])
    doc.set("warp_deformer.keyform_binding_band_indices", [band_index])
    doc.set("warp_deformer.keyform_begin_indices", [0])
    doc.set("warp_deformer.keyform_counts", [len(grids)])
    doc.set("warp_deformer.vertex_counts", [9])
    doc.set("warp_deformer.rows", [2])
    doc.set("warp_deformer.cols", [2])
    doc.set("warp_deformer_keyform.opacities", [1.0] * len(grids))

    positions = list(doc.get("keyform_position.xys"))
    begins = []
    for grid in grids:
        positions += [0.0] * (-len(positions) % 16)
        begins.append(len(positions))
        for x, y in grid:
            positions += [float(x), float(y)]
    doc.set("keyform_position.xys", positions)
    doc.counts[CI.KEYFORM_POSITIONS] = len(positions)
    doc.set("warp_deformer_keyform.keyform_position_begin_indices", begins)

    # V3_03：deformer 表非空时 additional 段必须非空，否则内核判 Header invalid
    doc.set("additional.quad_transforms", [0])
    doc.set("art_mesh.parent_deformer_indices", [0])
    return doc


def bind_to_parameter(doc):
    """把参数与变形器接上：1 条带 1 个绑定 2 个键（-30 / +30）。"""
    doc.counts[CI.KEYFORM_BINDING_BANDS] = 2
    doc.counts[CI.KEYFORM_BINDING_INDICES] = 1
    doc.counts[CI.KEYFORM_BINDINGS] = 1
    doc.counts[CI.KEYS] = 2
    doc.set("keyform_binding_band.begin_indices", [0, 0])
    doc.set("keyform_binding_band.counts", [0, 1])
    doc.set("keyform_binding_index.indices", [0])
    doc.set("keyform_binding.keys_begin_indices", [0])
    doc.set("keyform_binding.keys_counts", [2])
    doc.set("keys.values", [KEYS[0], KEYS[1]])
    doc.set("parameter.keyform_binding_begin_indices", [0])
    doc.set("parameter.keyform_binding_counts", [2])


def identity_grid():
    verts = [(GRID[i], GRID[i + 1]) for i in range(0, len(GRID), 2)]
    return verts


def shifted_grid(dx=SHIFT):
    return [(x + dx, y) for x, y in identity_grid()]


def report(tag: str, doc):
    OUT.mkdir(parents=True, exist_ok=True)
    moc3 = OUT / f"{tag}.moc3"
    moc3.write_bytes(doc.to_bytes())
    consistency = verify_moc3_consistency(str(moc3))
    if not consistency["ok"]:
        print(f"  {tag:22s} 一致性 False —— {consistency['blocker']}")
        return None
    manifest = write_package(OUT / tag, doc.to_bytes(), tag)
    loaded = verify_moc3_load(str(manifest))
    rest = render_probe(str(manifest), {PARAM: 0.0})
    moved = render_probe(str(manifest), {PARAM: KEYS[1]})
    stats = {"load_ok": loaded["ok"],
             "rest": (rest["alpha_bbox"], rest["opaque_pixels"],
                      rest["centroid"], rest["pixels_sha256"][:12]),
             "max": (moved["alpha_bbox"], moved["opaque_pixels"],
                     moved["centroid"], moved["pixels_sha256"][:12])}
    if not rest["ok"] or not moved["ok"]:
        print(f"  {tag:22s} 渲染失败 rest={rest['blocker']} max={moved['blocker']}")
        return stats
    dx = (moved["centroid"][0] - rest["centroid"][0]) if rest["centroid"] \
        and moved["centroid"] else None
    print(f"  {tag:22s} 一致=True 加载={loaded['ok']} "
          f"静止 bbox={rest['alpha_bbox']} px={rest['opaque_pixels']} "
          f"sha={rest['pixels_sha256'][:12]}")
    print(f"  {'':22s} 参数={KEYS[1]} bbox={moved['alpha_bbox']} "
          f"px={moved['opaque_pixels']} sha={moved['pixels_sha256'][:12]} "
          f"质心 dx={dx if dx is None else round(dx, 2)}")
    return stats


def main() -> int:
    print("== 基线：无变形器 ==")
    base = report("baseline_no_deformer", base_doc())

    print("\n== 恒等网格（单个关键形，无参数绑定）——必须与基线逐像素相同 ==")
    ident = report("warp_identity", warp(base_doc(), [identity_grid()]))

    print("\n== 参数驱动的 2 关键形 warp（+30 时网格右移 0.5 单位）==")
    doc = warp(base_doc(), [identity_grid(), shifted_grid()], band_index=1)
    bind_to_parameter(doc)
    driven = report("warp_driven", doc)

    print("\n== 判定 ==")
    if base and ident:
        same = base["rest"][3] == ident["rest"][3]
        print(f"  恒等网格与基线像素相同: {same}")
    if driven:
        print(f"  参数改变了画面: {driven['rest'][3] != driven['max'][3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
