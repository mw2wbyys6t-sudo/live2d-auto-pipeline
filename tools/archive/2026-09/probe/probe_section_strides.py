#!/usr/bin/env python3
"""用官方 Haru 的 SOT 差值反推每个 section 的元素字节数（含 runtime 段）。

运行：.venv/Scripts/python.exe tools/probe_section_strides.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live2d_builder.exporter import moc3_sections as ms  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"


def main() -> int:
    raw = HARU.read_bytes()
    layout = ms.build_layout(ms.MocVersion.V3_03)
    offsets = [struct.unpack_from("<I", raw, ms.HEADER_SIZE + 4 * i)[0]
               for i in range(len(layout))]
    counts = [struct.unpack_from("<I", raw,
                                 ms.DEFAULT_OFFSET + 4 * i)[0]
              for i in range(ms.COUNT_INFO_MAX)]
    print("section                                    count  偏移差   每元素字节  声明类型")
    for i, entry in enumerate(layout):
        if entry.group not in ("deformer", "warp_deformer", "rotation_deformer",
                               "warp_deformer_keyform", "rotation_deformer_keyform",
                               "part", "art_mesh"):
            continue
        if entry.count_idx < 0:
            continue
        count = counts[entry.count_idx]
        if count == 0:
            continue
        nxt = offsets[i + 1] if i + 1 < len(offsets) else len(raw)
        gap = nxt - offsets[i]
        # 段之间有对齐填充，用 gap 上界估算每元素字节
        per = gap / count
        flag = ""
        declared = ms.ELEM_SIZES.get(entry.elem_type, 0)
        if declared and abs(per - declared) > declared * 0.6:
            flag = f"  <-- gap/元素 {per:.1f} vs 声明 {declared}"
        print(f"  {entry.name:50s} {count:5d} {gap:8d}  {per:8.2f}   "
              f"{entry.elem_type}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
