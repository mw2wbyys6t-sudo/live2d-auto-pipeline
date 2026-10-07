#!/usr/bin/env python3
"""用官方 Haru 的 SOT 直接算出每个 section 的**真实字节数**（含 runtime / additional）。

前一段布局里唯一的歧义来自对齐填充，所以这里按「下一个非零偏移」而不是「下一个
section 偏移」计算长度：official 的 SOT 是单调的，section 之间只有填充。
运行：.venv/Scripts/python.exe tools/probe_haru_section_sizes.py
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
    sot = [struct.unpack_from("<I", raw, ms.HEADER_SIZE + 4 * i)[0]
           for i in range(ms.SOT_COUNT)]
    counts = struct.unpack_from(f"<{ms.COUNT_INFO_MAX}i",
                                raw, ms.DEFAULT_OFFSET)
    print(f"文件大小 {len(raw)}  SOT[0]={sot[0]} SOT[1]={sot[1]} "
          f"SOT 非零项 {sum(1 for v in sot if v)} 最后非零下标 "
          f"{max(i for i, v in enumerate(sot) if v)}")

    used = [i for i, v in enumerate(sot) if v]
    print("\n序号 section                                          count   偏移   "
          "到下个非零的差  每元素字节")
    for slot in used:
        if slot < 2:
            continue
        idx = slot - 2
        if idx >= len(layout):
            print(f"  {slot:3d} <超出布局表> 偏移 {sot[slot]}")
            continue
        entry = layout[idx]
        nxt = next((sot[j] for j in used if j > slot), len(raw))
        count = (len(raw) - sot[slot]) // entry.elem_size if entry.count_idx < 0 \
            else (counts[entry.count_idx] if entry.count_idx >= 0 else 1)
        size = nxt - sot[slot]
        declared = entry.elem_size if entry.elem_type != "runtime" \
            else ms.RUNTIME_UNIT_SIZE
        per = size / count if count else 0.0
        mark = "" if (count == 0 or abs(per - declared) < declared * 0.6) \
            else f"  <== 每元素 {per:.2f} vs 声明 {declared}"
        if (entry.elem_type == "runtime" or count == 0 or mark
                or "deformer" in entry.group):
            print(f"  {slot:3d} {entry.name:50s} {count:6d} {sot[slot]:7d} "
                  f"{size:10d}  {per:8.2f} {entry.elem_type}{mark}")

    add_off = sot[2 + len(layout) - 1]
    tail = raw[add_off:]
    print(f"\nadditional.quad_transforms @{add_off}：{len(tail)} 字节，"
          f"即 {len(tail) // 4} 个 bool；全零={not any(tail)}；"
          f"deformers={counts[ms.CountIdx.DEFORMERS]} "
          f"warp={counts[ms.CountIdx.WARP_DEFORMERS]} "
          f"rot={counts[ms.CountIdx.ROTATION_DEFORMERS]} "
          f"meshes={counts[ms.CountIdx.ART_MESHES]} "
          f"glues={counts[ms.CountIdx.GLUES]}")
    print("counts:", list(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
