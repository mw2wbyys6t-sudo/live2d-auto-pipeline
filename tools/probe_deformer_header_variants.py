#!/usr/bin/env python3
"""验证「Header section is invalid」的两个候选：格式版本 V3_03 与 additional 段长度。

内核在 L1（只加 deformer 公共表）报 Data section invalid，在 L2（补上 warp 子表）
改报 **Header section is invalid** —— 而官方 Haru 是 V3_00（99 段、没有 additional 段），
我们所有文档都是 V3_03（100 段，additional 为空）。所以先分诊这两件事。
运行：.venv/Scripts/python.exe tools/probe_deformer_header_variants.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from ladder_deformer_growth import (  # noqa: E402
    STEPS, add_deformer_table, add_warp_keyforms, add_warp_table, base_doc, link_mesh)
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402

OUT = ROOT / "Work" / "header-variants"


def build(steps):
    doc = base_doc()
    for apply in steps:
        apply(doc)
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
    print(f"  {tag:40s} 一致={str(result['ok']):5s} {reason}")
    return result["ok"]


def with_additional(doc, n_bools: int):
    doc.set("additional.quad_transforms", [0] * n_bools)
    return doc


def main() -> int:
    L1 = [add_deformer_table]
    L2 = [add_deformer_table, add_warp_table]
    L3 = L2 + [add_warp_keyforms]
    L4 = L3 + [link_mesh]

    if "--only-additional" in sys.argv:
        print("== V3_03 + 给 additional 段真实长度 ==")
        for n in (1, 2, 4, 16):
            for tag, steps in (("L2", L2), ("L4", L4)):
                check(f"{tag}_add{n}", with_additional(build(steps), n))
        return 0

    print("== 控制组（V3_03）==")
    check("L0_v303", build([]))
    check("L1_v303", build(L1))
    check("L2_v303", build(L2))

    print("\n== 换成 V3_00（不写 additional 段）==")
    for tag, steps in (("L0_v300", []), ("L1_v300", L1), ("L2_v300", L2),
                       ("L3_v300", L3), ("L4_v300", L4)):
        doc = build(steps)
        doc.version = ms.MocVersion.V3_00
        check(tag, doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
