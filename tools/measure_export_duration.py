#!/usr/bin/env python3
"""实测完整导出的耗时，给 Go /api/export/live2d 的超时预算一个依据（F-09）。

此前只有「3 张 64px 合成图层 ≈ 0.5s」这一个数据点，真实规模从没测过。
运行：.venv/Scripts/python.exe tools/measure_export_duration.py [--repeats 3]
输出：Work/export-duration.json + 终端表格
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from live2d_builder.pipeline import Live2DBuilder  # noqa: E402

OUT = ROOT / "Work" / "export-duration"

# 语义命名，确保变形器 / 键形 / 表达式这些真实路径都被走到
NAMES = [
    "hair_back", "hair_front", "hair_side", "face", "body", "clothes_inner",
    "clothes_outer", "eye_left", "eye_right", "brow_left", "brow_right",
    "mouth", "nose", "ear_left", "ear_right", "neck", "arm_left", "arm_right",
    "skirt", "leg_left", "leg_right", "accessory", "shadow", "highlight",
    "blush", "eyelash_left",
]


def make_layers(count: int, size: int) -> OrderedDict:
    from PIL import ImageDraw

    layers = OrderedDict()
    for i in range(count):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 大面积不透明内容：网格生成与烘焙的代价随像素量增长
        draw.rectangle([size // 8, size // 8, 7 * size // 8, 7 * size // 8],
                       fill=(40 + (i * 5) % 200, 90, 120, 255))
        draw.ellipse([size // 4, size // 4, 3 * size // 4, size // 2],
                     fill=(250, 240, 230, 255))
        layers[NAMES[i % len(NAMES)] + (f"_{i}" if i >= len(NAMES) else "")] = img
    return layers


def build_once(layers: OrderedDict, folder: Path) -> tuple:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    started = time.perf_counter()
    result = Live2DBuilder(output_dir=str(folder),
                           character_name="timing").build(layers)
    elapsed = time.perf_counter() - started
    ready = bool((result.get("moc3") or {}).get("runtime_ready"))
    return elapsed, ready


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    configs = [(3, 256), (8, 512), (16, 1024), (26, 1024), (12, 2048)]
    report = []
    print(f"{'图层':>4} {'边长':>5} {'百万像素':>9} {'次数':>4} "
          f"{'中位(s)':>8} {'最长(s)':>8} {'内核验收':>8}")
    for count, size in configs:
        layers = make_layers(count, size)
        samples = []
        ready_flags = []
        for i in range(args.repeats):
            elapsed, ready = build_once(layers, OUT / f"run_{count}x{size}_{i}")
            samples.append(round(elapsed, 2))
            ready_flags.append(ready)
            print(f"  {count}x{size} 第 {i + 1} 次: {elapsed:.2f}s "
                  f"runtime_ready={ready}", flush=True)
        row = {
            "layers": count, "edge_px": size,
            "megapixels": round(count * size * size / 1e6, 2),
            "seconds": samples,
            "median": statistics.median(samples),
            "max": max(samples),
            "runtime_ready_all": all(ready_flags),
        }
        report.append(row)
        print(f"{count:>4} {size:>5} {row['megapixels']:>9} "
              f"{len(samples):>4} {row['median']:>8} {row['max']:>8} "
              f"{str(row['runtime_ready_all']):>8}")

    (OUT / "export-duration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已写入 {OUT / 'export-duration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
