#!/usr/bin/env python3
"""大图安全审核工具：不整图塞进上下文，先探测尺寸，再按需生成缩略图/裁剪。

用法：
  python scripts/image_audit.py info <img>                 # 只报尺寸/模式/体积（不解码像素）
  python scripts/image_audit.py thumb <img> [--max 1024]   # 生成缩略图到 /tmp，返回路径
  python scripts/image_audit.py crop <img> --box x,y,w,h   # 区域裁剪（像素坐标）

原则：审核大图时绝不直接 Read 整图；先 info 拿尺寸，再 thumb 到 ≤1024 长边，
需要细节时用 crop 分块查看。避免把几十 MB 位图灌进上下文。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = 64_000_000  # 比 PIL 默认 89MP 更稳，防止炸弹


def cmd_info(path: str) -> None:
    p = Path(path)
    with Image.open(p) as im:
        size_kb = p.stat().st_size / 1024
        mp = (im.width * im.height) / 1_000_000
        print(f"file: {p}")
        print(f"size: {im.width}x{im.height}  mode: {im.mode}  {mp:.2f}MP  {size_kb:.0f}KB")
        print(f"建议: {'大图，先 thumb' if max(im.width, im.height) > 1024 else '可直接查看'}")


def cmd_thumb(path: str, max_side: int) -> str:
    out = Path("/tmp") / (Path(path).stem + f"_thumb_{max_side}.png")
    with Image.open(path) as im:
        im = im.convert("RGBA") if im.mode in ("P", "LA") else im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        im.save(out, optimize=True)
    print(out)
    return str(out)


def cmd_crop(path: str, box: tuple[int, int, int, int]) -> str:
    out = Path("/tmp") / (Path(path).stem + "_crop.png")
    with Image.open(path) as im:
        region = im.crop(box)
        region.save(out)
    print(out)
    return str(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("info")
    pi.add_argument("image")
    pt = sub.add_parser("thumb")
    pt.add_argument("image")
    pt.add_argument("--max", type=int, default=1024)
    pc = sub.add_parser("crop")
    pc.add_argument("image")
    pc.add_argument("--box", required=True, help="x,y,w,h 像素")
    args = ap.parse_args(argv)

    if args.cmd == "info":
        cmd_info(args.image)
    elif args.cmd == "thumb":
        cmd_thumb(args.image, args.max)
    elif args.cmd == "crop":
        x, y, w, h = (int(v) for v in args.box.split(","))
        cmd_crop(args.image, (x, y, x + w, y + h))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
