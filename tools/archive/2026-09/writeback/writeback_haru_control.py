#!/usr/bin/env python3
"""对照实验：把官方 Haru 读回再写出，还过得了内核吗？

这是解释上一个实验的必要前提 —— 如果参考实现在 deformer 段上本身就是有损的，
那么「我们的字节和它一致」并不能说明我们的编码正确。
运行：.venv/Scripts/python.exe tools/writeback_haru_control.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"
OUT = ROOT / "Work" / "writeback"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    original = HARU.read_bytes()
    print(f"原始 Haru: {len(original)} 字节")
    print("  内核一致性:", verify_moc3_consistency(str(HARU))["ok"])

    back = _core.Moc3.from_bytes(original)
    rewritten = back.to_bytes()
    path = OUT / "haru_reserialized.moc3"
    path.write_bytes(rewritten)
    print(f"\npy-moc3 重写: {len(rewritten)} 字节；与原文件相同={rewritten == original}")
    result = verify_moc3_consistency(str(path))
    print("  内核一致性:", result["ok"], result["blocker"] or "")

    if rewritten != original:
        first = next((i for i in range(min(len(original), len(rewritten)))
                      if original[i] != rewritten[i]), None)
        print(f"  第一个不同字节: {first}")
        print(f"    原: {original[first:first + 24].hex() if first is not None else '-'}")
        print(f"    新: {rewritten[first:first + 24].hex() if first is not None else '-'}")
        # 统计有多少个 section 的 SOT 偏移变了
        import struct
        from live2d_builder.exporter import moc3_sections as ms
        layout = ms.build_layout(ms.MocVersion.V3_03)
        diff = []
        for i, entry in enumerate(layout):
            o1 = struct.unpack_from("<I", original, ms.HEADER_SIZE + 4 * i)[0]
            o2 = struct.unpack_from("<I", rewritten, ms.HEADER_SIZE + 4 * i)[0]
            if o1 != o2:
                diff.append((entry.name, o1, o2))
        print(f"  SOT 偏移不同的 section 数: {len(diff)}")
        for row in diff[:10]:
            print(f"    {row[0]:50s} {row[1]} -> {row[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
