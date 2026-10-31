#!/usr/bin/env python3
"""Pygmalion Studio - 本地兜底图像生成器.

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


#: 能画中文的字体候选。占位图的水印要把用户的中文提示词原样画出来，
#: 而 PIL 默认字体没有 CJK 字形 —— 实测安装包上画出来是一排豆腐块，
#: 「如实标注」这句承诺在中文下就作废了。
_CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Dengxian.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _label_font(size: int = 16):
    """返回第一个可用的中文字体；一个都没有时返回 ``None``。"""
    from PIL import ImageFont

    for path in _CJK_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def _placeholder_label(seed: int, prompt: str, has_cjk_font: bool) -> str:
    """拼占位图水印。没有中文字体时**不画**中文提示词。

    拆成纯函数是为了可测：豆腐块（缺字形时的方框）只有在真渲染时才看得见，
    而这条规则本身跟 Pillow 无关。
    """
    head = "LOCAL-PLACEHOLDER · seed={}".format(seed)
    snippet = (prompt or "").strip().replace("\n", " ")[:72]
    if snippet and not has_cjk_font and not snippet.isascii():
        snippet = ""
    return f"{head} · {snippet}" if snippet else head


def _draw(width: int, height: int, seed: int, prompt: str) -> "Image.Image":
    from PIL import Image, ImageDraw, ImageFont

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
    font = _label_font()
    label = _placeholder_label(seed, prompt, font is not None)
    draw.rectangle([0, height - 28, width, height], fill=(0, 0, 0, 140))
    draw.text((8, height - 22), label, fill=(255, 255, 255, 230), font=font)
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
