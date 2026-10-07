#!/usr/bin/env python3
"""官方变形器是不是至少两个关键形？（内核可能拒绝单关键形的变形器）

运行：.venv/Scripts/python.exe tools/check_deformer_keyforms.py
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
    w = doc.get("warp_deformer.keyform_counts")
    r = doc.get("rotation_deformer.keyform_counts")
    print("warp keyform_counts 分布:", dict(sorted(Counter(w).items())))
    print("rotation keyform_counts 分布:", dict(sorted(Counter(r).items())))
    print("warp 最小值:", min(w), " rotation 最小值:", min(r))
    pk = doc.get("part.keyform_counts")
    ak = doc.get("art_mesh.keyform_counts")
    print("part keyform_counts 最小:", min(pk), " art_mesh 最小:", min(ak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
