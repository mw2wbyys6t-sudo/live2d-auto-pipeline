#!/usr/bin/env python3
"""反推官方 motion3.json 的 Meta 计数语义，用来校正我们生成的字段。

内核按 Meta 里的计数分配/校验缓冲，算错会直接在 LoadModelJson 里段错误
（实测：我们漏了 TotalPointCount 就崩），所以这几个数必须由官方样例定死。
运行：.venv/Scripts/python.exe tools/probe_motion3_meta_semantics.py
"""
from __future__ import annotations

import json
import pathlib
import sys

PATTERNS = (
    ("全线性", (2,)),
    ("全贝塞尔", (6,)),
)


def parse_segments(segments):
    """按官方规则走一遍：首段 [t,v]，其后线性 2 浮点、贝塞尔 6 浮点。

    判定依据：剩余浮点数减去 2 之后还能被 6 整除的位置才是贝塞尔段起点，
    否则按线性推进。返回 (段数, 关键帧数)。
    """
    pos = 2
    segments_count = 1
    points = 1
    while pos < len(segments):
        rest = len(segments) - pos
        is_bezier = rest >= 8 and (rest - 2) % 6 == 0
        step = 6 if is_bezier else 2
        pos += step
        segments_count += 1
        points += 1
    return segments_count, points


def main() -> int:
    files = sorted(pathlib.Path("Work/native-sample/Haru/motions").glob(
        "*.motion3.json"))
    if not files:
        print("没有官方 motion 样例可分析")
        return 1

    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        meta = doc.get("Meta") or {}
        floats = sum(len(c["Segments"]) for c in doc["Curves"])
        seg_pairs, point_pairs = 0, 0
        for curve in doc["Curves"]:
            segs, pts = parse_segments(curve["Segments"])
            seg_pairs += segs
            point_pairs += pts
        print(f"{path.name:30s} 曲线={len(doc['Curves']):3d} "
              f"浮点合计={floats:5d} 走出的段数={seg_pairs:4d} 关键帧={point_pairs:4d}")
        print(f"{'':30s} Meta: CurveCount={meta.get('CurveCount')} "
              f"TotalSegmentCount={meta.get('TotalSegmentCount')} "
              f"TotalPointCount={meta.get('TotalPointCount')} "
              f"keys={sorted(meta)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
