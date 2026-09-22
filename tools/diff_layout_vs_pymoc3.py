#!/usr/bin/env python3
"""把我们的 moc3_sections 与 py-moc3 的 SECTION_LAYOUT 逐项对齐比较。

运行：.venv/Scripts/python.exe tools/diff_layout_vs_pymoc3.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402


def main() -> int:
    theirs = list(_core.SECTION_LAYOUT)
    ours = list(ms.SECTION_LAYOUT)
    print(f"py-moc3 段数 {len(theirs)}；我们 {len(ours)}")
    print(f"ADDITIONAL_V303: py-moc3 {len(_core.ADDITIONAL_V303) if isinstance(_core.ADDITIONAL_V303, list) else 1} 项；"
          f"我们 {len(ms.ADDITIONAL_V303)} 项")
    only_theirs = [e.name for e in theirs if e.name not in {o.name for o in ours}]
    only_ours = [o.name for o in ours if o.name not in {e.name for e in theirs}]
    print("只在 py-moc3 里:", only_theirs or "无")
    print("只在我们里:", only_ours or "无")

    mismatch = 0
    by_name = {o.name: o for o in ours}
    for i, e in enumerate(theirs):
        o = by_name.get(e.name)
        if o is None:
            continue
        if (e.elem_type, e.count_idx, e.align) != (o.elem_type, o.count_idx, o.align):
            print(f"  位置 {i} {e.name}: py-moc3=({e.elem_type},{e.count_idx},{e.align}) "
                  f"我们=({o.elem_type},{o.count_idx},{o.align})")
            mismatch += 1
        if i != ours.index(o):
            print(f"  顺序不同 {e.name}: py-moc3 #{i} vs 我们 #{ours.index(o)}")
            mismatch += 1
    print(f"类型/计数/对齐/顺序不一致: {mismatch}")

    print("\n== count_idx 指向 DEFORMERS/WARP_DEFORMERS 的段（按 py-moc3 顺序）==")
    for i, e in enumerate(theirs):
        if e.count_idx in (_core.CountIdx.DEFORMERS, _core.CountIdx.WARP_DEFORMERS,
                           _core.CountIdx.ROTATION_DEFORMERS,
                           _core.CountIdx.WARP_DEFORMER_KEYFORMS):
            print(f"  #{i:3d} {e.name:52s} {e.elem_type:7s} align={e.align}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
