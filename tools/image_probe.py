#!/usr/bin/env python3
"""大图安全审核工具（战场 B）：不把整张大图塞进 AI 上下文。

用途：给图片出「尺寸/字节/格式」事实，并按需生成 ≤1024px 缩略图或区域裁剪，
供 AI/人审核。只读，不改原图。

用法：
  python tools/image_probe.py <image>                 # 打印尺寸/大小/模式/是否超阈值
  python tools/image_probe.py <image> --thumb out.png # 生成最长边 ≤1024 缩略图
  python tools/image_probe.py <image> --crop x,y,w,h --thumb out.png
  python tools/image_probe.py <image> --grid 3x3     # 切九宫格路径清单（分批查看）

阈值：最长边 > 2048px 或 > 5MB 标记为「大图」，建议走缩略图/裁剪而非整图读取。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

THUMB_MAX_EDGE = 1024
BIG_EDGE = 2048
BIG_MB = 5.0


def probe(path: Path) -> dict:
    from PIL import Image

    with Image.open(path) as im:
        return {
            "size": im.size,
            "mode": im.mode,
            "format": im.format,
            "bytes": path.stat().st_size,
            "mb": round(path.stat().st_size / 1024 / 1024, 2),
            "big": max(im.size) > BIG_EDGE or path.stat().st_size > BIG_MB * 1024 * 1024,
        }


def make_thumb(src: Path, dst: Path, max_edge: int = THUMB_MAX_EDGE) -> None:
    from PIL import Image

    with Image.open(src) as im:
        scale = min(1.0, max_edge / float(max(im.size)))
        out = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                        Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(dst)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--thumb", help="缩略图输出路径")
    ap.add_argument("--crop", help="x,y,w,h 区域（配合 --thumb 裁剪）")
    ap.add_argument("--grid", help="行列，如 3x3，输出各格路径清单")
    args = ap.parse_args(argv)

    src = Path(args.image)
    if not src.is_file():
        print(f"找不到文件: {src}", file=sys.stderr)
        return 2

    info = probe(src)
    print(f"文件: {src}")
    print(f"尺寸: {info['size'][0]}x{info['size'][1]}  模式: {info['mode']}  格式: {info['format']}")
    print(f"大小: {info['mb']} MB  大图: {'是' if info['big'] else '否'}")

    if args.thumb:
        crop = None
        if args.crop:
            x, y, w, h = (int(v) for v in args.crop.split(","))
            crop = (x, y, x + w, y + h)
        from PIL import Image

        with Image.open(src) as im:
            region = im.crop(crop) if crop else im
            scale = min(1.0, THUMB_MAX_EDGE / float(max(region.size)))
            out = region.resize((max(1, round(region.width * scale)),
                                 max(1, round(region.height * scale))), Image.LANCZOS)
            dst = Path(args.thumb)
            dst.parent.mkdir(parents=True, exist_ok=True)
            out.save(dst)
            print(f"缩略图: {dst} ({out.size[0]}x{out.size[1]})")

    if args.grid:
        from PIL import Image

        cols, rows = (int(v) for v in args.grid.lower().split("x"))
        with Image.open(src) as im:
            gw, gh = im.width // cols, im.height // rows
            base = src.stem
            for r in range(rows):
                for c in range(cols):
                    cell = im.crop((c * gw, r * gh, (c + 1) * gw, (r + 1) * gh))
                    p = src.parent / f"{base}_r{r}c{c}.png"
                    cell.save(p)
                    print(f"  {p}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
