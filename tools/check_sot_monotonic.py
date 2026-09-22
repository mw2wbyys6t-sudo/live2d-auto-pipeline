#!/usr/bin/env python3
"""校验 Haru 的 SOT 偏移是否按 section 顺序单调递增。

若不是，则「用相邻偏移差反推元素大小」的做法无效，得换办法量 runtime 段。
运行：.venv/Scripts/python.exe tools/check_sot_monotonic.py
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
    bad = [(i, layout[i].name, offsets[i], layout[i + 1].name, offsets[i + 1])
           for i in range(len(layout) - 1) if offsets[i] > offsets[i + 1]]
    print(f"section 数 {len(layout)}；偏移非单调的位置数 {len(bad)}")
    for row in bad[:8]:
        print(f"  #{row[0]} {row[1]} off={row[2]} > 下一段 {row[3]} off={row[4]}")
    zero = sum(1 for o in offsets if o == 0)
    print(f"偏移为 0（空段）的数量: {zero}")
    print(f"最小非零偏移 {min(o for o in offsets if o)}，最大 {max(offsets)}，"
          f"文件长度 {len(raw)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
