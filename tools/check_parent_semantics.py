#!/usr/bin/env python3
"""官方 Haru 里 art_mesh 的父级语义：部件与变形器是互斥还是并存？

运行：.venv/Scripts/python.exe tools/check_parent_semantics.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"


def main() -> int:
    doc = _core.Moc3.from_file(str(HARU))
    parts = doc.get("art_mesh.parent_part_indices")
    defs = doc.get("art_mesh.parent_deformer_indices")
    combo = Counter((p < 0, d < 0) for p, d in zip(parts, defs))
    print("组合 (parent_part 为 -1, parent_deformer 为 -1) 的计数：")
    for (no_part, no_def), n in sorted(combo.items()):
        print(f"  part={'-1' if no_part else '>=0'}  deformer={'-1' if no_def else '>=0'}: {n} 个网格")
    print("\nparent_part_indices 取值分布:", dict(Counter(parts)))
    print("parent_deformer_indices 是否为 -1 的数量:", sum(1 for d in defs if d < 0))

    dparts = doc.get("deformer.parent_part_indices")
    ddefs = doc.get("deformer.parent_deformer_indices")
    print("\ndeformer.parent_part_indices 中 -1 的数量:",
          sum(1 for v in dparts if v < 0), "/", len(dparts))
    print("deformer.parent_deformer_indices 中 -1 的数量:",
          sum(1 for v in ddefs if v < 0), "/", len(ddefs))
    print("deformer.parent_part_indices 样例:", dparts[:12])

    # 变形器引用的 part 是否总是其下网格的 part
    linked = {}
    for i, d in enumerate(defs):
        if d >= 0:
            linked.setdefault(d, []).append(parts[i])
    both = sum(1 for d, ps in linked.items() if dparts[d] in ps)
    print(f"\n挂在变形器 d 下的网格，其 part 是否等于 d 的 parent_part：{both} / {len(linked)} 个变形器")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
