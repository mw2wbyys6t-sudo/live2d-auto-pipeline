#!/usr/bin/env python3
"""诊断：带 warp 变形器的文档里，哪些 section 的长度与 count 不符。

运行：.venv/Scripts/python.exe tools/diagnose_deformer_doc.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.probe_warp_grid_space import spec_with_deformer  # noqa: E402
from live2d_builder.exporter import moc3_sections as ms  # noqa: E402
from live2d_builder.exporter.moc3_lint import lint_document  # noqa: E402
from live2d_builder.exporter.moc3_model import compile_static_rig  # noqa: E402
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency  # noqa: E402


def main() -> int:
    doc = compile_static_rig(spec_with_deformer(100.0, "row_up"))
    print("== count info 里非零的槽位 ==")
    for i, value in enumerate(doc.counts):
        if value:
            print(f"  slot {i:2d} = {value}")
    print("\n== 长度与 count 不符的 section ==")
    bad = 0
    for entry in ms.build_layout(doc.version):
        if entry.count_idx < 0 or entry.elem_type == "runtime":
            continue
        expected = doc.counts[entry.count_idx]
        actual = len(doc.get(entry.name))
        if expected and actual != expected:
            print(f"  {entry.name:52s} 期望 {expected} 实得 {actual}")
            bad += 1
    print(f"  （{bad} 项不符）")

    print("\n== 值为默认/未显式设置但 count>0 的段 ==")
    for entry in ms.build_layout(doc.version):
        if doc.counts[entry.count_idx if entry.count_idx >= 0 else 0] == 0 \
                and entry.count_idx >= 0:
            continue
        if not doc.get(entry.name) and entry.elem_type != "runtime":
            print(f"  {entry.name} 未写入（count={doc.counts[entry.count_idx]}）")

    print("\n== lint ==")
    for issue in lint_document(doc):
        print("  ", issue)
    print("\n== 官方一致性 ==")
    out = ROOT / "Work" / "diag_deformer.moc3"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(out))
    print("  ok =", result["ok"], "blocker =", result["blocker"])
    print("  stdout:", result["stdout"][-400:])
    print("  stderr:", result["stderr"][-300:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
