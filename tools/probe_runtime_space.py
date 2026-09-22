#!/usr/bin/env python3
"""看官方 Haru 的 runtime / 未处理段到底装了什么。

运行：.venv/Scripts/python.exe tools/probe_runtime_space.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moc3 import _core  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402

HARU = ROOT / "Work" / "native-sample" / "Haru" / "Haru.moc3"


def main() -> int:
    doc = _core.Moc3.from_file(str(HARU))
    print("== 官方 Haru 的 runtime 段 ==")
    for entry in ms.build_layout(ms.MocVersion.V3_03):
        if entry.elem_type != "runtime":
            continue
        try:
            value = doc.get(entry.name)
        except Exception as exc:  # noqa: BLE001
            print(f"  {entry.name}: 读取失败 {exc}")
            continue
        print(f"  {entry.name:36s} count_idx={entry.count_idx} "
              f"len={len(value) if hasattr(value, '__len__') else value} "
              f"样例={str(value)[:120]}")
    print("\n== 我们的容器怎么处理 runtime 段 ==")
    from live2d_builder.exporter.moc3_container import Moc3Container
    probe = Moc3Container(version=ms.MocVersion.V3_03)
    probe.counts[ms.CountIdx.DEFORMERS] = 1
    raw = probe.to_bytes()
    print(f"  未写 deformer.runtime_space 时，整体长度 {len(raw)}")
    print(f"  SOT[deformer.runtime_space] 偏移是否为 0: "
          f"{int.from_bytes(raw[64 + 0 * 4:68 + 0 * 4], 'little') == 0}")
    import struct
    idx = [i for i, e in enumerate(ms.build_layout(ms.MocVersion.V3_03))
           if e.name == "deformer.runtime_space"][0]
    off = struct.unpack_from("<I", raw, 64 + idx * 4)[0]
    print(f"  deformer.runtime_space 在 SOT 第 {idx} 槽，偏移 {off}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
