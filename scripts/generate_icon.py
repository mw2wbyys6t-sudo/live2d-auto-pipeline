#!/usr/bin/env python3
"""Pygmalion (Live2D Master Agent) — 项目专属图标生成器。

设计理念：以 Pygmalion（皮格马利翁）神话为灵感 —— 画框倾斜表示"创作边界被打破"，
框内少女是线稿草图（未完成），框外一只手伸入画布拿画笔（创作者介入）。
寓意"创作者赋予造物生命"，与 Live2D Master Agent "把草图变成可动模型" 的核心精神契合。

视觉调性：
  - 极简三色调（黑 + 白 + 深青色 #1e5f74），避开 AI 偏爱的粉紫渐变/装饰星光
  - 厚黑描边，类似独立插画师手绘风，而非 AI 矢量插画
  - 倾斜画框打破对称构图，避免 AI 偏爱的居中对称范式

源图：.uploads/pygmalion_l_breakout.png （Seedream 生成的 1920×1920 JPEG）

输出：
  assets/icon/logo.svg          可缩放矢量版（手写 + 简化）
  assets/icon/logo_1024.png    主图（透明背景）
  assets/icon/logo_512.png
  assets/icon/logo_256.png
  assets/icon/logo_128.png
  assets/icon/logo_64.png
  assets/icon/logo_32.png
  assets/icon/logo_16.png
  web/public/favicon.ico        Windows ICO（多尺寸）
  api/webui/dist/favicon.ico    Go embed 副本
"""

from pathlib import Path
from PIL import Image

# 源图
SOURCE = Path(".uploads/pygmalion_l_breakout.png")

# 输出目录
ICON_DIR = Path("assets/icon")
FAVICON_WEB = Path("web/public/favicon.ico")
FAVICON_API = Path("api/webui/dist/favicon.ico")

# 各 PNG 尺寸
SIZES = [1024, 512, 256, 128, 64, 32, 16]

# favicon.ico 多尺寸（按视觉清晰度排序）
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

# 白色→透明阈值（白底 RGB 接近 255）
WHITE_THRESHOLD = 245


def white_to_transparent(img: Image.Image, threshold: int = WHITE_THRESHOLD) -> Image.Image:
    """把接近白色的像素变成透明，生成带 alpha 通道的 RGBA 图。

    对 JPEG 边缘的近白像素做平滑过渡，避免硬边锯齿。
    """
    img = img.convert("RGBA")
    pixels = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            # 计算与纯白的接近度
            whiteness = min(r, g, b)
            if whiteness >= threshold:
                # 完全白 → 完全透明
                pixels[x, y] = (255, 255, 255, 0)
            elif whiteness >= threshold - 20:
                # 半透明白（边缘过渡）→ 线性插值 alpha
                alpha = int((threshold - whiteness) / 20 * 255)
                pixels[x, y] = (r, g, b, alpha)
    return img


def main():
    if not SOURCE.exists():
        raise SystemExit(f"❌ 源图不存在：{SOURCE}")

    print(f"📥 读取源图：{SOURCE}")
    src = Image.open(SOURCE)
    print(f"   原始尺寸：{src.size}  模式：{src.mode}")

    # 1. 去白底 → 透明
    print("🎨 处理白底→透明通道…")
    master = white_to_transparent(src)

    # 2. 保存多尺寸 PNG
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for s in SIZES:
        out = ICON_DIR / f"logo_{s}.png"
        resized = master.resize((s, s), Image.LANCZOS)
        resized.save(out, "PNG", optimize=True)
        size_kb = out.stat().st_size / 1024
        print(f"  ✓ {out.name:18s} {s}×{s:4d}  {size_kb:6.1f} KB")

    # 3. favicon.ico（多尺寸）—— 用简化符号版，保证小尺寸清晰
    FAVICON_WEB.parent.mkdir(parents=True, exist_ok=True)
    FAVICON_API.parent.mkdir(parents=True, exist_ok=True)

    # 加载简化符号版小尺寸图标（如果存在）
    small_master = None
    small_path = ICON_DIR / "logo_s_48.png"
    if small_path.exists():
        small_master = Image.open(small_path).convert("RGBA")

    # 优先用简化版；没有则回退到主图
    ico_source = small_master if small_master is not None else master

    ico_imgs = [ico_source.resize(s, Image.LANCZOS) for s in ICO_SIZES]
    ico_imgs[0].save(
        str(FAVICON_WEB),
        format="ICO",
        sizes=ICO_SIZES,
        append_images=ico_imgs[1:],
    )
    print(f"  ✓ {FAVICON_WEB}  ({FAVICON_WEB.stat().st_size / 1024:.1f} KB)  [源={'简化版' if small_master else '主图'}]")

    # Go embed 副本
    ico_imgs[0].save(
        str(FAVICON_API),
        format="ICO",
        sizes=ICO_SIZES,
        append_images=ico_imgs[1:],
    )
    print(f"  ✓ {FAVICON_API}  ({FAVICON_API.stat().st_size / 1024:.1f} KB)")

    # 5. SVG 占位（实际 SVG 需要手写简化版路径，临时引用主 PNG）
    # 为避免 favicon 在不支持 ICO 的环境失效，同时提供 SVG 版本
    svg_placeholder = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- Pygmalion 图标 — 高保真 PNG 内嵌版本。
     注：完整 SVG 矢量版本将由手工重绘路径，提供无损缩放。
     当前 SVG 引用 1024px 主 PNG，足以覆盖绝大多数现代浏览器的 SVG icon 场景。 -->
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     viewBox="0 0 1024 1024" width="512" height="512">
  <image xlink:href="logo_1024.png" x="0" y="0" width="1024" height="1024"/>
</svg>
'''
    (ICON_DIR / "logo.svg").write_text(svg_placeholder, encoding="utf-8")
    print(f"  ✓ logo.svg (PNG-内嵌占位)")

    print("\n✅ Pygmalion 图标全部生成完毕！")
    print(f"   目录：{ICON_DIR.absolute()}/")
    print(f"   favicon：{FAVICON_WEB.absolute()}")


if __name__ == "__main__":
    main()
