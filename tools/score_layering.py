#!/usr/bin/env python3
"""
CLI over :mod:`core.segment_engine.layer_quality`.

The checks themselves live in ``core/`` because the workflow gates on them and
``tools/`` is not copied into the Docker image (see Dockerfile). This file only
prints.

Run:
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tools/score_layering.py \
        --layers Work/sam2-gd-cpu/layers [--foreground Work/.../optimized_x.png] \
        [--json out.json] [--selftest]

Exit codes: 0 every check holds, 1 at least one violated, 2 nothing to score.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.segment_engine.layer_quality import (  # noqa: E402
    REQUIRED_PARTS,
    foreground_of,
    score,
    selftest,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layers", help="directory of part PNGs to score")
    ap.add_argument("--json", default=None)
    ap.add_argument("--require", default=",".join(REQUIRED_PARTS),
                    help="comma-separated parts that must have a mask")
    ap.add_argument("--foreground", default=None,
                    help="RGBA image whose alpha is the character silhouette; "
                         "enables the leak/unexplained checks")
    ap.add_argument("--selftest", action="store_true",
                    help="run the negative controls and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.layers:
        print("[FAIL] 需要 --layers（或 --selftest）")
        return 2

    layers_dir = Path(args.layers)
    if not layers_dir.is_dir():
        print(f"[FAIL] 目录不存在: {layers_dir}")
        return 2

    foreground = None
    if args.foreground:
        fpath = Path(args.foreground)
        if not fpath.is_file():
            print(f"[FAIL] 轮廓图不存在: {fpath}")
            return 2
        foreground = foreground_of(Image.open(fpath))
        if foreground is None:
            print(f"[FAIL] {fpath} 没有可用的 alpha 轮廓（全不透明或全透明）")
            return 2

    require = tuple(p.strip() for p in args.require.split(",") if p.strip())
    result = score(layers_dir, require, foreground)
    if "error" in result:
        print(f"[FAIL] {result['error']}")
        return 2

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"{'part':<16}{'pixels':>8}{'coverage':>10}{'cc':>4}"
          f"{'largest':>9}{'bbox':>24}")
    print("-" * 71)
    for part, row in result["parts"].items():
        bbox = ",".join(str(v) for v in row["bbox"])
        print(f"{part:<16}{row['pixel_count']:>8}{row['coverage']:>10.6f}"
              f"{row['components']:>4}{row['largest_cc_share']:>9.2f}{bbox:>24}")
    print("-" * 71)
    print(f"parts={result['part_count']}  union_coverage={result['union_coverage']:.6f}")
    if result.get("explained_fraction") is not None:
        print(f"explained_fraction={result['explained_fraction']:.4f}")
    if result["missing_parts"]:
        print(f"missing={','.join(result['missing_parts'])}"
              "（未出掩码；是否算缺陷看 --require）")

    if not result["defects"]:
        print("\n=== PASS: 全部一致性判据成立 ===")
        if args.json:
            print(f"[out] json -> {args.json}")
        return 0

    print(f"\n=== {result['defect_count']} 条判据被违反 ===")
    for group, items in result["defects"].items():
        for msg in items:
            print(f"  [{group}] {msg}")
    for msg in result.get("amodal_notes", []):
        print(f"  [amodal] {msg}")
    if args.json:
        print(f"\n[out] json -> {args.json}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
