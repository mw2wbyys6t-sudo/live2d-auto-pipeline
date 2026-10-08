"""小尺寸专用简化版 — "画框破界" 抽象符号。

设计原则：
  - 极简三色：黑边框 + 白底框 + 深青色手
  - 仅保留 L 方案的"画框倾斜 + 手破界"两个核心符号
  - 去线条稿少女（在小尺寸下不可识别）
  - 与 L 方案主图共享语义，但视觉上是新符号

尺寸：48 / 32 / 16（这些尺寸直接走简化版，不走主图缩放）
"""
from pathlib import Path
from PIL import Image, ImageDraw

ICON_DIR = Path("assets/icon")
SIZE = 1024  # 内部画布，分辨率高质量，输出时会缩放

# 三色调
BLACK = (15, 15, 15, 255)
DEEP_CYAN = (30, 95, 116, 255)
WHITE = (255, 255, 255, 255)


def draw_frame_breakout(size: int = SIZE) -> Image.Image:
    """符号：倾斜画框 + 框外深青手破界。

    与 L 方案主图共享"破界"语义，但抽掉了所有细节。
    """
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))  # 透明背景
    draw = ImageDraw.Draw(img)

    # 计算画框参数（4° 倾斜，与主图大致一致）
    # 画框是个圆角矩形（简化版无圆角，纯锐角矩形，更接近"草图未完成"）
    # 框占据画布 60-65%
    cx, cy = size / 2, size / 2
    fw = int(size * 0.62)  # 框宽
    fh = int(size * 0.74)  # 框高（比宽大，类似 4:3）
    bbox = [
        cx - fw / 2, cy - fh / 2,
        cx + fw / 2, cy + fh / 2,
    ]

    # 用透明图像做旋转
    # 1. 先在临时 RGBA 上画框和框内白底
    frame_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fdraw = ImageDraw.Draw(frame_layer)
    # 框内白底
    fdraw.rectangle(
        [
            cx - fw / 2 + int(size * 0.03),
            cy - fh / 2 + int(size * 0.03),
            cx + fw / 2 - int(size * 0.03),
            cy + fh / 2 - int(size * 0.03),
        ],
        fill=WHITE,
    )
    # 框外深色边框（粗描边）
    border_w = int(size * 0.05)
    fdraw.rectangle(bbox, outline=BLACK, width=border_w)

    # 2. 旋转 4°
    rotated = frame_layer.rotate(4, resample=Image.BICUBIC, expand=False)

    # 3. 合成到主图
    img.paste(rotated, (0, 0), rotated)

    # 4. 画"破界"手：从画框外右下方伸入
    #    简化版：手变成色块 + 笔的极简轮廓
    #    手位置：右下角，跨过边框
    hand_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(hand_layer)

    # 手掌：圆角色块，跨过画框右下边
    palm_w = int(size * 0.20)
    palm_h = int(size * 0.22)
    palm_cx = int(size * 0.78)
    palm_cy = int(size * 0.66)
    hdraw.rounded_rectangle(
        [
            palm_cx - palm_w / 2,
            palm_cy - palm_h / 2,
            palm_cx + palm_w / 2,
            palm_cy + palm_h / 2,
        ],
        radius=int(palm_w * 0.25),
        fill=DEEP_CYAN,
    )

    # 画笔：从手心向左上方斜出，穿过画框
    pen_w = int(size * 0.025)
    pen_len = int(size * 0.30)
    # 起笔点（在手掌内）
    sx = palm_cx - int(palm_w * 0.1)
    sy = palm_cy - int(palm_h * 0.1)
    # 终点（穿过画框向画框内中心方向）
    ex = palm_cx - int(palm_w * 0.6) - pen_len
    ey = palm_cy - int(palm_h * 0.6) - pen_len
    # 用矩形代替斜线（pen_w 厚度）
    # 简化：用 ImageDraw.line
    hdraw.line([(sx, sy), (ex, ey)], fill=DEEP_CYAN, width=pen_w)

    # 笔尖小三角（深色点）
    hdraw.ellipse(
        [
            ex - pen_w * 1.2, ey - pen_w * 1.2,
            ex + pen_w * 1.2, ey + pen_w * 1.2,
        ],
        fill=BLACK,
    )

    # 手不需要旋转（仅画框倾斜）
    img.paste(hand_layer, (0, 0), hand_layer)

    return img


def main():
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    # 输出尺寸：小尺寸专用
    small_sizes = [48, 32, 16]

    master = draw_frame_breakout()
    for s in small_sizes:
        out = ICON_DIR / f"logo_s_{s}.png"
        master.resize((s, s), Image.LANCZOS).save(out, "PNG", optimize=True)
        print(f"  ✓ {out.name:18s} {s}×{s:4d}  {out.stat().st_size / 1024:5.1f} KB")

    print("\n✅ 小尺寸符号版图标生成完毕")


if __name__ == "__main__":
    main()
