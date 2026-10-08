#!/usr/bin/env python3
"""Live2D Master Agent — 项目专属图标生成器。

设计理念：二次元角色风 —— 以「标志性大眼 + AI 星火」为核心意象。
眼睛是二次元角色最具辨识度的符号，即使缩到 16×16 favicon 也能认出。
配色沿用项目品牌粉 #ff69b4 → 紫渐变。

输出：
  assets/icon/logo.svg          可缩放矢量版
  assets/icon/logo_512.png      高清 PNG
  assets/icon/logo_256.png
  assets/icon/logo_128.png
  assets/icon/logo_64.png
  assets/icon/logo_32.png
  assets/icon/logo_16.png
  web/public/favicon.ico        Windows ICO（多尺寸）
"""

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

# ─── 品牌色 ───
PINK = (255, 107, 193, 255)
PURPLE = (168, 85, 247, 255)
IRIS_BLUE = (99, 102, 241, 255)
IRIS_DARK = (49, 46, 129, 255)
SPARKLE_GOLD = (251, 191, 36, 255)
WHITE = (255, 255, 255, 255)
FACE_CREAM = (255, 252, 250, 255)
BLUSH = (255, 182, 193, 255)

SIZE = 1024  # 主画布


def lerp_color(c1, c2, t):
    """线性插值两个 RGBA 颜色。"""
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(4))


def draw_gradient_rounded_rect(draw, bbox, radius, c_top_left, c_bottom_right):
    """画一个带对角渐变的圆角矩形（逐行填充近似）。"""
    x0, y0, x1, y1 = [int(v) for v in bbox]
    w = x1 - x0
    h = y1 - y0
    radius = int(radius)
    # 先画纯色圆角矩形作为底
    mid = lerp_color(c_top_left, c_bottom_right, 0.5)
    draw.rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius=radius, fill=mid)
    # 逐行叠加渐变（用半透明线模拟）
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / max(h - 1, 1)
        row_color = lerp_color(c_top_left, c_bottom_right, t)
        # 从左到右也加一点渐变（对角感）
        for x in range(0, w, 4):  # 步进 4 提速
            xt = x / max(w - 1, 1)
            blended = lerp_color(row_color, c_bottom_right, xt * 0.3)
            gdraw.rectangle([x, y, x + 3, y], fill=blended)
    # 用圆角矩形 mask 裁剪渐变
    mask = Image.new("L", (w, h), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    base.paste(grad, (x0, y0), mask)
    return base


def draw_anime_eye(draw, cx, cy, ew, eh, iris_color, looking_right=False):
    """画一只二次元风格的大眼。

    结构（从底到顶）：
    1. 眼白椭圆（底）
    2. 虹膜圆（彩色）
    3. 瞳孔（深色小圆）
    4. 大高光（白色，标志性）
    5. 小高光（白色点）
    """
    # 眼白
    draw.ellipse(
        [cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2],
        fill=WHITE,
    )
    # 虹膜
    iris_r = min(ew, eh) * 0.62
    ix = cx + (iris_r * 0.08 if looking_right else -iris_r * 0.08)
    iy = cy + iris_r * 0.1
    draw.ellipse(
        [ix - iris_r, iy - iris_r, ix + iris_r, iy + iris_r],
        fill=iris_color,
    )
    # 瞳孔
    pup_r = iris_r * 0.45
    draw.ellipse(
        [ix - pup_r, iy - pup_r, ix + pup_r, iy + pup_r],
        fill=IRIS_DARK,
    )
    # 大高光（左上）
    hl_r = iris_r * 0.35
    hl_x = ix - iris_r * 0.3
    hl_y = iy - iris_r * 0.35
    draw.ellipse(
        [hl_x - hl_r, hl_y - hl_r, hl_x + hl_r, hl_y + hl_r],
        fill=WHITE,
    )
    # 小高光（右下）
    sh_r = iris_r * 0.15
    sh_x = ix + iris_r * 0.35
    sh_y = iy + iris_r * 0.3
    draw.ellipse(
        [sh_x - sh_r, sh_y - sh_r, sh_x + sh_r, sh_y + sh_r],
        fill=WHITE,
    )


def draw_sparkle(draw, cx, cy, size, color=SPARKLE_GOLD):
    """画一个四角星（AI 星火）。"""
    points = []
    arms = 4
    inner = size * 0.18
    for i in range(arms * 2):
        angle = (math.pi / arms) * i - math.pi / 2
        r = size if i % 2 == 0 else inner
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=color)


def draw_small_star(draw, cx, cy, r, color=WHITE):
    """画一个小圆点星。"""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def render_icon(size=SIZE):
    """渲染图标主画面，返回 RGBA Image。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── 1. 渐变圆角背景（squircle）──
    margin = size * 0.04
    radius = size * 0.22
    bg = draw_gradient_rounded_rect(
        draw,
        [margin, margin, size - margin, size - margin],
        radius=radius,
        c_top_left=PINK,
        c_bottom_right=PURPLE,
    )
    img = Image.alpha_composite(img, bg)
    draw = ImageDraw.Draw(img)

    # ── 2. 脸部底色圆（柔和的奶白，让眼睛更突出）──
    face_r = size * 0.30
    face_cx = size * 0.5
    face_cy = size * 0.52
    # 轻微发光
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse(
        [face_cx - face_r - size * 0.04, face_cy - face_r - size * 0.04,
         face_cx + face_r + size * 0.04, face_cy + face_r + size * 0.04],
        fill=(255, 255, 255, 50),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.03))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)
    # 脸
    draw.ellipse(
        [face_cx - face_r, face_cy - face_r, face_cx + face_r, face_cy + face_r],
        fill=FACE_CREAM,
    )

    # ── 3. 两只大眼（核心意象）──
    eye_w = size * 0.14
    eye_h = size * 0.18
    eye_y = face_cy - size * 0.02
    eye_offset = size * 0.085
    draw_anime_eye(draw, face_cx - eye_offset, eye_y, eye_w, eye_h, IRIS_BLUE)
    draw_anime_eye(draw, face_cx + eye_offset, eye_y, eye_w, eye_h, IRIS_BLUE)

    # ── 4. 腮红（可爱元素）──
    blush_r = size * 0.035
    blush_y = face_cy + size * 0.08
    blush_offset = size * 0.13
    blush_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(blush_img)
    for bx in (face_cx - blush_offset, face_cx + blush_offset):
        bdraw.ellipse([bx - blush_r, blush_y - blush_r * 0.6,
                       bx + blush_r, blush_y + blush_r * 0.6],
                      fill=(255, 150, 180, 140))
    blush_img = blush_img.filter(ImageFilter.GaussianBlur(size * 0.01))
    img = Image.alpha_composite(img, blush_img)
    draw = ImageDraw.Draw(img)

    # ── 5. 嘴巴（小微笑）──
    mouth_w = size * 0.06
    mouth_h = size * 0.025
    mouth_cx = face_cx
    mouth_cy = face_cy + size * 0.13
    draw.pieslice(
        [mouth_cx - mouth_w, mouth_cy - mouth_h,
         mouth_cx + mouth_w, mouth_cy + mouth_h],
        start=10, end=170, fill=(220, 80, 120, 255), width=max(1, int(size * 0.006)),
    )

    # ── 6. AI 星火（四角星 + 小圆点）──
    # 大星火：左上角
    draw_sparkle(draw, size * 0.22, size * 0.22, size * 0.06)
    # 大星火：右下角
    draw_sparkle(draw, size * 0.80, size * 0.78, size * 0.05)
    # 小星火点缀
    draw_sparkle(draw, size * 0.82, size * 0.25, size * 0.035, color=WHITE)
    draw_sparkle(draw, size * 0.18, size * 0.78, size * 0.03, color=WHITE)
    # 小圆点星
    draw_small_star(draw, size * 0.30, size * 0.15, size * 0.012)
    draw_small_star(draw, size * 0.72, size * 0.88, size * 0.010)
    draw_small_star(draw, size * 0.88, size * 0.50, size * 0.008)
    draw_small_star(draw, size * 0.12, size * 0.50, size * 0.008)

    return img


def write_svg(path: Path):
    """手写一份可缩放 SVG 版本（与光栅版风格一致）。"""
    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="512" height="512">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ff6bc1"/>
      <stop offset="100%" stop-color="#a855f7"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="52%" r="32%">
      <stop offset="0%" stop-color="#fffdfa" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="#fffdfa" stop-opacity="0"/>
    </radialGradient>
  </defs>

  <!-- 圆角背景 -->
  <rect x="40" y="40" width="944" height="944" rx="225" ry="225" fill="url(#bg)"/>

  <!-- 脸部发光底 -->
  <circle cx="512" cy="532" r="340" fill="url(#glow)"/>
  <!-- 脸 -->
  <circle cx="512" cy="532" r="307" fill="#fffcfa"/>

  <!-- 左眼 -->
  <g>
    <ellipse cx="425" cy="512" rx="72" ry="92" fill="#ffffff"/>
    <circle cx="422" cy="525" r="57" fill="#6366f1"/>
    <circle cx="422" cy="525" r="26" fill="#312e81"/>
    <circle cx="405" cy="505" r="20" fill="#ffffff"/>
    <circle cx="442" cy="540" r="9" fill="#ffffff"/>
  </g>

  <!-- 右眼 -->
  <g>
    <ellipse cx="599" cy="512" rx="72" ry="92" fill="#ffffff"/>
    <circle cx="602" cy="525" r="57" fill="#6366f1"/>
    <circle cx="602" cy="525" r="26" fill="#312e81"/>
    <circle cx="585" cy="505" r="20" fill="#ffffff"/>
    <circle cx="622" cy="540" r="9" fill="#ffffff"/>
  </g>

  <!-- 腮红 -->
  <ellipse cx="377" cy="600" rx="36" ry="20" fill="#ff96b4" opacity="0.6"/>
  <ellipse cx="647" cy="600" rx="36" ry="20" fill="#ff96b4" opacity="0.6"/>

  <!-- 嘴 -->
  <path d="M 482 666 Q 512 684 542 666" stroke="#dc5078" stroke-width="7"
        fill="none" stroke-linecap="round"/>

  <!-- AI 星火 -->
  <g fill="#fbbf24">
    <path d="M 225 225 L 245 285 L 285 305 L 245 325 L 225 385 L 205 325 L 165 305 L 205 285 Z"/>
    <path d="M 820 800 L 835 842 L 868 855 L 835 868 L 820 910 L 805 868 L 772 855 L 805 842 Z"/>
  </g>
  <g fill="#ffffff">
    <path d="M 840 255 L 852 285 L 875 295 L 852 305 L 840 335 L 828 305 L 805 295 L 828 285 Z"/>
    <path d="M 185 800 L 195 822 L 215 830 L 195 838 L 185 860 L 175 838 L 155 830 L 175 822 Z"/>
    <circle cx="307" cy="154" r="12"/>
    <circle cx="737" cy="901" r="10"/>
    <circle cx="901" cy="512" r="8"/>
    <circle cx="123" cy="512" r="8"/>
  </g>
</svg>'''
    path.write_text(svg, encoding="utf-8")


def main():
    out_dir = Path("assets/icon")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 渲染主图标
    print("正在渲染 1024×1024 主图标…")
    master = render_icon(SIZE)

    # 保存多种尺寸 PNG
    sizes = [512, 256, 128, 64, 32, 16]
    pngs = []
    for s in sizes:
        resized = master.resize((s, s), Image.LANCZOS)
        path = out_dir / f"logo_{s}.png"
        resized.save(path, "PNG")
        pngs.append(resized)
        print(f"  ✓ {path.name} ({s}×{s})")

    # 保存无背景透明版（用于深色场景）
    master.save(out_dir / "logo_1024.png", "PNG")
    print(f"  ✓ logo_1024.png (原画)")

    # 生成 favicon.ico（多尺寸嵌入）
    favicon_dir = Path("web/public")
    favicon_dir.mkdir(parents=True, exist_ok=True)
    ico_sizes = [(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)]
    ico_images = [master.resize(s, Image.LANCZOS) for s in ico_sizes]
    favicon_dir.joinpath("favicon.ico").write_bytes(b"")  # touch
    ico_images[0].save(
        str(favicon_dir / "favicon.ico"),
        format="ICO",
        sizes=ico_sizes,
    )
    print(f"  ✓ web/public/favicon.ico (多尺寸)")

    # 也放一份到 api/webui/dist（Go embed 需要）
    dist = Path("api/webui/dist")
    dist.mkdir(parents=True, exist_ok=True)
    ico_images[0].save(str(dist / "favicon.ico"), format="ICO", sizes=ico_sizes)
    print(f"  ✓ api/webui/dist/favicon.ico")

    # 写 SVG
    write_svg(out_dir / "logo.svg")
    print(f"  ✓ logo.svg (可缩放矢量)")

    # README 用 banner（带文字）
    print("\n正在生成 README banner…")
    banner = master.resize((256, 256), Image.LANCZOS)
    banner.save(out_dir / "logo_256.png", "PNG")

    print("\n✅ 图标全部生成完毕！")
    print(f"   目录: {out_dir}/")
    print(f"   favicon: {favicon_dir}/favicon.ico")


if __name__ == "__main__":
    main()
