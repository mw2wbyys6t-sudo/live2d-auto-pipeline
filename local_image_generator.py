#!/usr/bin/env python3
"""Live2D Master Agent - 本地兜底图像生成器.

用途：在所有云端 provider 都不可用（无 API Key / 断网）时，保证
「生成 → 分层 → 导出 → 预览」主链路依然可以走通 —— 产出一张
**确定性**的立绘占位图（渐变背景 + 简单角色剪影 + 提示词水印），
并如实标注 ``LOCAL-PLACEHOLDER``，绝不冒充 AI 生成结果。

被 Go 后端 api/services/image_generator.go 调用：

    python local_image_generator.py --width 1024 --height 1024 \
        --steps 25 --seed 42 --quality standard -- "提示词"

输出：最后一行打印「图片已保存: <绝对路径>.png」，Go 按该约定解析。
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import sys
import time
from pathlib import Path


def _seeded_palette(seed: int):
    """由 seed 确定性地派生一组柔和配色（背景渐变 + 主色 + 辅色）。"""
    rng = hashlib.sha256(str(seed).encode()).digest()

    def hue(i: int) -> float:
        return rng[i] / 255.0

    def pastel(h: float, s: float = 0.45, v: float = 0.92):
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return int(r * 255), int(g * 255), int(b * 255), 255

    return pastel(hue(0), 0.35, 0.96), pastel(hue(1), 0.35, 0.86), pastel(hue(2)), pastel(hue(3))


def _draw(width: int, height: int, seed: int, prompt: str) -> "Image.Image":
    from PIL import Image, ImageDraw

    top, bottom, primary, accent = _seeded_palette(seed)
    img = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(img)

    # 垂直渐变背景
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
        draw.line([(0, y), (width, y)], fill=color)

    # 角色剪影：头 + 颈 + 肩（居中，占高约 70%）
    cx = width // 2
    head_r = int(min(width, height) * 0.16)
    head_cy = int(height * 0.34)
    neck_w = int(head_r * 0.5)
    draw.ellipse(
        [cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
        fill=primary,
    )
    draw.rectangle(
        [cx - neck_w // 2, head_cy + head_r - 4, cx + neck_w // 2, head_cy + head_r + neck_w],
        fill=primary,
    )
    shoulder_top = head_cy + head_r + neck_w
    draw.ellipse(
        [cx - int(width * 0.30), shoulder_top - head_r,
         cx + int(width * 0.30), shoulder_top + int(height * 0.4)],
        fill=accent,
    )
    # 眼睛（两点），让剪影更像「立绘」
    eye_dy = int(head_r * 0.15)
    eye_dx = int(head_r * 0.42)
    eye_r = max(3, head_r // 8)
    for sx in (-1, 1):
        draw.ellipse(
            [cx + sx * eye_dx - eye_r, head_cy + eye_dy - eye_r,
             cx + sx * eye_dx + eye_r, head_cy + eye_dy + eye_r],
            fill=(40, 40, 48, 255),
        )

    # 提示词水印（截断），保证占位来源一目了然
    label = "LOCAL-PLACEHOLDER · seed={}".format(seed)
    snippet = (prompt or "").strip().replace("\n", " ")[:72]
    if snippet:
        label = f"{label} · {snippet}"
    draw.rectangle([0, height - 28, width, height], fill=(0, 0, 0, 140))
    draw.text((8, height - 22), label, fill=(255, 255, 255, 230))
    return img


def main() -> int:
    parser = argparse.ArgumentParser(description="本地兜底立绘占位生成器")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--quality", type=str, default="standard")
    parser.add_argument("--model", type=str, default="")
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("prompt", nargs="*", help="提示词（Go 以 -- 分隔传入）")
    args = parser.parse_args()

    from core.config import config

    prompt = " ".join(args.prompt).strip()
    seed = args.seed if args.seed else int(time.time())

    out_dir = Path(args.output) if args.output else Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"local_placeholder_{int(time.time())}.png"

    try:
        img = _draw(args.width, args.height, seed, prompt)
        img.save(out_path)
    except Exception as e:  # 缺 Pillow 等硬故障时如实失败
        print(f"[local_image_generator] 生成失败: {e}", file=sys.stderr)
        return 1

    print(f"图片已保存: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
