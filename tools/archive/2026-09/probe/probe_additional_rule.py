#!/usr/bin/env python3
"""定出 V3_03 的 additional.quad_transforms 到底要几个元素。

已实测：deformer 表非空而 additional 段为空 -> 内核判 Header section invalid；
补 1 个 bool 就一致。这里用 2 个、3 个变形器分辨「每变形器一个」与「只要非空」。
运行：.venv/Scripts/python.exe tools/probe_additional_count_rule.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402
from ladder_deformer_growth import GRID, base_doc  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402
from live2d_builder.exporter.moc3_lint import lint_document  # noqa: E402

OUT = ROOT / "Work" / "additional-rule"
CI = ms.CountIdx
KEYFORMS_PER_GRID = 9          # rows=cols=2 -> 3x3 顶点


def build(n_deformers: int, rotation_at: int = -1):
    """base_doc + n 个变形器（每个自带 warp/rotation 子表行与一份单位网格）。"""
    doc = base_doc()
    n_warp = sum(1 for i in range(n_deformers) if i != rotation_at)
    n_rot = n_deformers - n_warp

    doc.counts[CI.DEFORMERS] = n_deformers
    doc.counts[CI.WARP_DEFORMERS] = n_warp
    doc.counts[CI.ROTATION_DEFORMERS] = n_rot
    doc.counts[CI.WARP_DEFORMER_KEYFORMS] = n_warp
    doc.counts[CI.ROTATION_DEFORMER_KEYFORMS] = n_rot

    positions = list(doc.get("keyform_position.xys"))
    begins = []
    for _ in range(n_deformers):
        positions += [0.0] * (-len(positions) % 16)
        begins.append(len(positions))
        positions += [float(v) for v in GRID]
    doc.set("keyform_position.xys", positions)
    doc.counts[CI.KEYFORM_POSITIONS] = len(positions)

    warp_i = rot_i = 0
    types, specific = [], []
    for i in range(n_deformers):
        if i == rotation_at:
            types.append(1)
            specific.append(rot_i)
            rot_i += 1
        else:
            types.append(0)
            specific.append(warp_i)
            warp_i += 1
    doc.set("deformer.ids", [f"Def{i}" for i in range(n_deformers)])
    doc.set("deformer.keyform_binding_band_indices", [0] * n_deformers)
    doc.set("deformer.visibles", [1] * n_deformers)
    doc.set("deformer.enables", [1] * n_deformers)
    doc.set("deformer.parent_part_indices", [0] * n_deformers)
    doc.set("deformer.parent_deformer_indices", [-1] * n_deformers)
    doc.set("deformer.types", types)
    doc.set("deformer.specific_indices", specific)

    doc.set("warp_deformer.keyform_binding_band_indices", [0] * n_warp)
    doc.set("warp_deformer.keyform_begin_indices", list(range(n_warp)))
    doc.set("warp_deformer.keyform_counts", [1] * n_warp)
    doc.set("warp_deformer.vertex_counts", [KEYFORMS_PER_GRID] * n_warp)
    doc.set("warp_deformer.rows", [2] * n_warp)
    doc.set("warp_deformer.cols", [2] * n_warp)
    doc.set("warp_deformer_keyform.opacities", [1.0] * n_warp)
    doc.set("warp_deformer_keyform.keyform_position_begin_indices",
            [b for b, t in zip(begins, types) if t == 0])

    if n_rot:
        doc.set("rotation_deformer.keyform_binding_band_indices", [0] * n_rot)
        doc.set("rotation_deformer.keyform_begin_indices", list(range(n_rot)))
        doc.set("rotation_deformer.keyform_counts", [1] * n_rot)
        doc.set("rotation_deformer.base_angles", [0.0] * n_rot)
        doc.set("rotation_deformer_keyform.opacities", [1.0] * n_rot)
        doc.set("rotation_deformer_keyform.angles", [0.0] * n_rot)
        doc.set("rotation_deformer_keyform.origin_xs", [0.0] * n_rot)
        doc.set("rotation_deformer_keyform.origin_ys", [0.0] * n_rot)
        doc.set("rotation_deformer_keyform.scales", [1.0] * n_rot)
        doc.set("rotation_deformer_keyform.reflect_xs", [0] * n_rot)
        doc.set("rotation_deformer_keyform.reflect_ys", [0] * n_rot)

    doc.set("art_mesh.parent_deformer_indices", [n_deformers - 1])
    return doc


def check(tag: str, doc) -> bool:
    path = OUT / f"{tag}.moc3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(path))
    reason = ""
    if not result["ok"]:
        for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
            if "csmHasMocConsistency" in line:
                reason = line.split("csmHasMocConsistency:")[-1].strip()
    issues = [str(i) for i in lint_document(doc)]
    print(f"  {tag:34s} 一致={str(result['ok']):5s} "
          f"{reason or ('lint=' + (issues[:1][0] if issues else '干净'))}")
    return result["ok"]


def main() -> int:
    for n in (1, 2, 3):
        for extra in (0, 1, n, n + 1):
            doc = build(n)
            doc.set("additional.quad_transforms", [0] * extra)
            check(f"{n}_deformers_add{extra}", doc)
    for extra in (0, 1, 2):
        doc = build(2, rotation_at=0)
        doc.set("additional.quad_transforms", [0] * extra)
        check(f"warp1+rot1_add{extra}", doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
