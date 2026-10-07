#!/usr/bin/env python3
"""官方 Haru 的三角形手性约定：在 y 向上的模型坐标里是 CCW 还是 CW？

运行：.venv/Scripts/python.exe tools/check_haru_winding.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from moc3 import _core  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"


def main() -> int:
    doc = _core.Moc3.from_file(str(HARU))
    pos = np.asarray(doc.get("keyform_position.xys"), dtype=float).reshape(-1, 2)
    idx = np.asarray(doc.get("position_index.indices"), dtype=int)
    begins = doc.get("art_mesh.position_index_begin_indices")
    counts = doc.get("art_mesh.vertex_counts")
    total_cw = total_ccw = 0
    for begin, count in zip(begins, counts):
        tri = idx[begin:begin + count].reshape(-1, 3)
        p = pos[tri]                      # (n,3,2) 关键形 0
        a, b, c = p[:, 0], p[:, 1], p[:, 2]
        cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - \
                (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        total_ccw += int((cross > 0).sum())
        total_cw += int((cross < 0).sum())
    print(f"官方 Haru：{len(begins)} 个网格，三角形 CCW={total_ccw} CW={total_cw} "
          f"（y 向上模型坐标）")
    print("→ 约定:" , "CCW 朝前" if total_ccw > total_cw else "CW 朝前")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
