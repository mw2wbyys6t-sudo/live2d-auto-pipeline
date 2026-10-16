"""统一图片尺寸策略（单一事实来源）。

把「大图」从异常变成可处理：所有进入生产管线的图片在入口被等比规范化到
安全上限，而不是被拒绝或直通 OOM。前端（canvas 降采样）与后端（PIL 规范化）
共用同一组语义常量。

策略：
- ``MAX_SIDE_PX``：单边像素硬上限（4096，与 Go 导入炸弹防护对齐）。超过即等比缩小。
- ``MAX_MEGAPIXELS``：总像素软上限（约 16MP，4K≈8.3MP 以内不动，8K≈67MP 缩小）。
- 保比例、保 alpha、Lanczos 重采样；不裁切、不改变构图。
"""

from __future__ import annotations

from PIL import Image

# 单边像素硬上限（Go imaging.go 的 maxImageDimension 同源）。
MAX_SIDE_PX = 4096
# 总像素软上限（百万像素）。超过则按面积等比缩小到该总量。
MAX_MEGAPIXELS = 16.0


def _megapixels(w: int, h: int) -> float:
    return (w * h) / 1_000_000.0


def normalize_image(img: Image.Image, max_side: int = MAX_SIDE_PX,
                    max_mp: float = MAX_MEGAPIXELS) -> tuple[Image.Image, dict]:
    """把图片规范化到安全尺寸内，返回 (新图, 记录)。

    记录含 ``original_size``、``final_size``、``resized``(bool)、``scale``。
    已是安全尺寸则原样返回（resized=False），不复制、不重编码。
    """
    w, h = img.size
    changed = False
    scale = 1.0

    # 单边超限
    longest = max(w, h)
    if longest > max_side:
        scale = min(scale, max_side / float(longest))
        changed = True

    # 总面积超限（按面积平方根等比）
    mp = _megapixels(w, h)
    if mp > max_mp:
        area_scale = (max_mp / mp) ** 0.5
        scale = min(scale, area_scale)
        changed = True

    record = {
        "original_size": [w, h],
        "final_size": [w, h],
        "resized": False,
        "scale": 1.0,
    }
    if not changed or scale >= 1.0:
        return img, record

    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    record.update({
        "final_size": [new_w, new_h],
        "resized": True,
        "scale": round(scale, 4),
    })
    return resized, record


def normalize_path(path: str, max_side: int = MAX_SIDE_PX,
                   max_mp: float = MAX_MEGAPIXELS) -> tuple[Image.Image, dict]:
    """打开图片并规范化，返回 (RGBA 图, 记录)。打开失败抛原始异常。"""
    img = Image.open(path)
    img.load()
    rgba = img.convert("RGBA")
    return normalize_image(rgba, max_side=max_side, max_mp=max_mp)
