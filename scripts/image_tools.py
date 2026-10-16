#!/usr/bin/env python3
"""大图安全处理工具（战场 B）：审核 AI 资产时不把整图塞入上下文。

用法（均为只读/写缩略图到输出目录，不改动原图）：
    python scripts/image_tools.py probe <image> [more...]
        打印每张图的尺寸 / 模式 / 体积，定位超大图。
    python scripts/image_tools.py thumb <image> --out <dir> [--max-edge 1024]
        生成 ≤1024 长边的缩略图（供分析），不读取像素进终端。
    python scripts/image_tools.py crop <image> --box x,y,w,h --out <dir>
        按区域裁剪出小块，分批查看细节。

规程：审核 docs/assets/*.png 等大图时，一律先 probe 看尺寸，再 thumb 到 ≤1024
后分析；需要局部细节用 crop，禁止把原始大 PNG 整图读入上下文。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # 工具本身需要读大图做缩略，关闭 DecompressionBomb 警告


def probe(paths: list[Path]) -> None:
    for p in paths:
        with Image.open(p) as im:
            mb = p.stat().st_size / 1e6
            print(f"{p}: {im.size[0]}x{im.size[1]} {im.mode} {mb:.2f}MB")


def thumb(src: Path, out_dir: Path, max_edge: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGBA")
        w, h = im.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / float(longest)
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        dest = out_dir / f"{src.stem}_thumb{im.size[0]}x{im.size[1]}.png"
        im.save(dest)
        print(f"缩略图 -> {dest} ({im.size[0]}x{im.size[1]})")
        return dest


def crop(src: Path, box: tuple[int, int, int, int], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        region = im.crop(box)
        dest = out_dir / f"{src.stem}_crop_{box[0]}_{box[1]}.png"
        region.save(dest)
        print(f"裁剪 -> {dest} {region.size}")
        return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="大图安全处理工具（审核不爆上下文）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="打印尺寸/模式/体积")
    p_probe.add_argument("images", nargs="+", type=Path)

    p_thumb = sub.add_parser("thumb", help="生成缩略图")
    p_thumb.add_argument("image", type=Path)
    p_thumb.add_argument("--out", required=True, type=Path)
    p_thumb.add_argument("--max-edge", type=int, default=1024)

    p_crop = sub.add_parser("crop", help="区域裁剪 x,y,w,h")
    p_crop.add_argument("image", type=Path)
    p_crop.add_argument("--box", required=True, help="x,y,w,h")
    p_crop.add_argument("--out", required=True, type=Path)

    args = ap.parse_args()
    if args.cmd == "probe":
        probe(args.images)
    elif args.cmd == "thumb":
        thumb(args.image, args.out, args.max_edge)
    elif args.cmd == "crop":
        x, y, w, h = (int(v) for v in args.box.split(","))
        crop(args.image, (x, y, x + w, y + h), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
