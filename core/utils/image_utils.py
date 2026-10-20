#!/usr/bin/env python3
"""
Live2D Master Agent - Image Utility Functions

Provides common image loading, saving, resizing, background removal,
enhancement, compositing, and preview utilities used across the pipeline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from core.logger import get_logger

log = get_logger("utils.image")

# Optional deps with graceful fallbacks
try:
    import cv2  # type: ignore
    HAS_CV2 = True
except ImportError:  # pragma: no cover
    HAS_CV2 = False
    log.debug("opencv-python not available; some image ops will use PIL fallbacks")

try:
    import rembg  # type: ignore
    HAS_REMBG = True
except ImportError:  # pragma: no cover
    HAS_REMBG = False


def load_image(path: str) -> Image.Image:
    """Load an image from disk with validation.

    Args:
        path: Filesystem path to the image.

    Returns:
        PIL Image in RGBA mode.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If the file cannot be decoded as an image.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    try:
        img = Image.open(p)
        img.load()  # force decode now to surface errors early
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Failed to decode image '{path}': {exc}") from exc
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    log.debug(f"Loaded image {path} size={img.size} mode={img.mode}")
    return img


def save_image(img: Image.Image, path: str, quality: int = 95) -> str:
    """Save a PIL image to disk, creating parent directories as needed.

    Args:
        img: PIL Image to save.
        path: Destination path. Extension determines format.
        quality: JPEG/WEBP quality (0-100). PNG ignores this.

    Returns:
        The absolute path the image was written to.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    save_kwargs = {}
    if ext in (".jpg", ".jpeg"):
        if img.mode == "RGBA":
            img = img.convert("RGB")
        save_kwargs.update(quality=quality, optimize=True)
    elif ext == ".webp":
        save_kwargs.update(quality=quality, method=6)
    elif ext == ".png":
        save_kwargs.update(optimize=True)
    img.save(out, **save_kwargs)
    log.debug(f"Saved image -> {out} ({out.stat().st_size} bytes)")
    return str(out.resolve())


def resize_to_max(img: Image.Image, max_dim: int = 2048) -> Image.Image:
    """Resize an image so its longest edge is at most ``max_dim`` pixels.

    Aspect ratio is preserved. If the image is already within bounds it is
    returned unchanged.

    Args:
        img: Source PIL Image.
        max_dim: Maximum allowed dimension (width or height).

    Returns:
        Resized PIL Image (Lanczos resampling).
    """
    if max_dim <= 0:
        raise ValueError(f"max_dim must be positive, got {max_dim}")
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / float(longest)
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    log.debug(f"Resizing {img.size} -> {new_size} (max_dim={max_dim})")
    return img.resize(new_size, Image.LANCZOS)


def remove_background(img: Image.Image, method: str = "auto") -> Image.Image:
    """Remove the background from a character image.

    Strategy order when ``method='auto'``:
      1. ``rembg`` (U2Net/ISNet) if installed.
      2. Corner-color thresholding as a PIL/numpy fallback.

    Args:
        img: Source image (RGB or RGBA). Converted to RGBA internally.
        method: One of ``"auto"``, ``"rembg"``, ``"corner"``.

    Returns:
        RGBA image with transparent background.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    method = (method or "auto").lower()
    if method == "rembg" or (method == "auto" and HAS_REMBG):
        try:
            log.debug("Removing background with rembg")
            out = rembg.remove(img)  # type: ignore[union-attr]
            if out.mode != "RGBA":
                out = out.convert("RGBA")
            return out
        except Exception as exc:  # pragma: no cover - model/runtime errors
            log.warning(f"rembg failed ({exc}); falling back to corner threshold")

    return _remove_background_corner(img)


def estimate_foreground_silhouette(rgb: np.ndarray, tolerance: float = 26.0) -> np.ndarray:
    """从画面自身估算角色剪影（布尔掩码，``True`` 为前景）。

    做法：取四边窄环的颜色当"背景调色板"，把**与背景同色且与画布边框连通**
    的像素判为背景，其余为前景；再闭运算补网格线造成的缝、开运算削掉标注
    文字与引线的细笔画、保留最大连通块并填洞。

    * 必须"与边框连通"而不是单纯比色：立绘裙子的藏蓝与背景墙几乎同色（实测
      都在 (45,60,82) 附近），单纯比色会把裙子一起掏空；裙子在角色轮廓内部、
      不与画布边框连通，这一条约束正好把它保住。
    * 必须"只留最大连通块"：白色标注文字/引线跟背景反差极大，比色一定判成
      前景，只有"不与角色主体连通"这一条能把它们摘掉。

    估歪了（纯色画布、剪影贴满边框……）时返回全 ``True``：宁可退回"整幅图
    都是角色"，也不要交出一张空掩码。

    Args:
        rgb: ``(H, W, 3)`` 的 RGB 数组。
        tolerance: 与背景调色板的 RGB 欧氏距离容差。
    """
    rgb = np.asarray(rgb)
    h, w = rgb.shape[:2]
    ones = np.ones((h, w), dtype=bool)
    if not HAS_CV2:
        return ones

    img = rgb.astype(np.int32)
    band = max(2, min(h, w) // 100)
    ring = np.concatenate([
        rgb[:band].reshape(-1, 3),
        rgb[-band:].reshape(-1, 3),
        rgb[:, :band].reshape(-1, 3),
        rgb[:, -band:].reshape(-1, 3),
    ])
    # 量化到 8 级再统计，避免为每个像素建调色板；只保留足够常见的颜色
    quantised = (ring // 8) * 8
    colors, counts = np.unique(quantised, axis=0, return_counts=True)
    palette = colors[counts >= max(4, int(len(ring) * 0.002))].astype(np.int32)
    if palette.size == 0:
        return ones

    best = np.full((h, w), 1 << 30, dtype=np.int32)
    for color in palette:
        diff = img - color
        np.minimum(best, (diff * diff).sum(axis=2), out=best)
    background_like = best <= tolerance ** 2

    _, labels = cv2.connectedComponents(background_like.astype(np.uint8), connectivity=4)
    border = set(labels[0].tolist()) | set(labels[-1].tolist()) | \
        set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    border.discard(0)
    background = (
        np.isin(labels, np.fromiter(border, dtype=np.int32))
        if border else background_like
    )
    foreground = (~background).astype(np.uint8)

    foreground = cv2.morphologyEx(
        foreground, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
    )
    foreground = cv2.morphologyEx(
        foreground, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )

    _, labels, stats, _ = cv2.connectedComponentsWithStats(foreground, 8)
    if len(stats) > 1:
        foreground = (labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)

    # 填洞：与画布边框连通的非前景才是真背景，其余凹陷都属于角色内部
    _, labels = cv2.connectedComponents((1 - foreground).astype(np.uint8), connectivity=4)
    outside = set(labels[0].tolist()) | set(labels[-1].tolist()) | \
        set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    outside.discard(0)
    enclosed = (
        ~np.isin(labels, np.fromiter(outside, dtype=np.int32))
        if outside else np.ones((h, w), dtype=bool)
    )
    foreground = (foreground > 0) | enclosed

    coverage = float(foreground.mean())
    if coverage < 0.02 or coverage > 0.95:
        log.warning(
            f"Silhouette estimate degenerate (coverage {coverage:.1%}); "
            f"treating the whole canvas as foreground"
        )
        return ones
    return foreground


def _silhouette_alpha(mask: np.ndarray) -> np.ndarray:
    """剪影掩码 -> alpha 通道，边缘做一像素羽化。"""
    alpha = (np.asarray(mask, dtype=bool).astype(np.uint8) * 255)
    if HAS_CV2:
        alpha = cv2.GaussianBlur(alpha, (3, 3), 0.7)
    return alpha


def cutout_by_silhouette(img: Image.Image) -> Image.Image:
    """按自估剪影抠图（不依赖任何模型，也不会触发模型下载）。

    与 :func:`remove_background` 的区别：后者在 rembg 可用时会优先用学习式
    模型，而本地没有权重时 ``rembg.remove`` 会**静默联网下载**。优化步骤要求
    确定性，所以直接走这里。
    """
    arr = np.array(img.convert("RGBA"))
    mask = estimate_foreground_silhouette(arr[:, :, :3])
    arr[:, :, 3] = np.minimum(arr[:, :, 3], _silhouette_alpha(mask))
    return Image.fromarray(arr, "RGBA")


def _remove_background_corner(img: Image.Image) -> Image.Image:
    """基于自估剪影的背景移除。

    旧实现按"与四角中位色的距离"逐个像素判透明。深色网格墙的明暗渐变和
    砖缝让距离大量越界，实测整幅图仍有 37.7% 被判成前景（背景墙、网格线、
    标注文字全在里面），后续分层把背景算成头发、把文字算成上衣。
    现在改为先估出**连通**剪影，再按剪影给 alpha。
    """
    return cutout_by_silhouette(img)


def enhance_for_layering(img: Image.Image) -> Image.Image:
    """Enhance an RGBA image to improve layer separation quality.

    Applies a gentle contrast boost, sharpening, and mild color quantization
    to make K-means / color-based segmentation more robust.

    Args:
        img: Source RGBA image.

    Returns:
        Enhanced RGBA image.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    out = img.copy()
    out = ImageEnhance.Contrast(out).enhance(1.25)
    out = ImageEnhance.Sharpness(out).enhance(1.6)
    out = ImageEnhance.Color(out).enhance(1.1)
    # Mild quantization on RGB only to keep alpha clean
    alpha = out.split()[-1]
    rgb_q = out.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=64).convert("RGB")
    out = rgb_q.convert("RGBA")
    out.putalpha(alpha)
    return out


def composite_layers(
    layers: Sequence[Image.Image],
    positions: Optional[Sequence[Tuple[int, int]]] = None,
    size: Optional[Tuple[int, int]] = None,
    background: Optional[Tuple[int, int, int, int]] = None,
) -> Image.Image:
    """Alpha-composite a stack of RGBA layers.

    Args:
        layers: Ordered back-to-front sequence of PIL Images.
        positions: Optional (x, y) offsets per layer. Defaults to (0, 0).
        size: Output canvas (w, h). Defaults to the max extent of layers.
        background: RGBA background fill tuple, or ``None`` for transparent.

    Returns:
        Composited RGBA image.
    """
    if not layers:
        raise ValueError("composite_layers requires at least one layer")

    norm_layers = [l.convert("RGBA") for l in layers]
    if positions is None:
        positions = [(0, 0)] * len(norm_layers)
    elif len(positions) != len(norm_layers):
        raise ValueError("positions length must match layers length")

    if size is None:
        max_w = max(p[0] + l.size[0] for p, l in zip(positions, norm_layers))
        max_h = max(p[1] + l.size[1] for p, l in zip(positions, norm_layers))
        size = (max(max_w, 1), max(max_h, 1))

    if background is None:
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    else:
        canvas = Image.new("RGBA", size, background)

    for layer, (x, y) in zip(norm_layers, positions):
        canvas.alpha_composite(layer, dest=(x, y))
    return canvas


def create_preview(
    layers: Sequence[Image.Image],
    labels: Optional[Sequence[str]] = None,
    cell: int = 256,
    cols: int = 4,
    background: Tuple[int, int, int] = (32, 32, 32),
) -> Image.Image:
    """Create a labeled grid preview of layers for QA / debugging.

    Args:
        layers: RGBA layer images to display.
        labels: Optional label strings (one per layer). Auto-numbered if None.
        cell: Cell size in pixels (square).
        cols: Number of columns.
        background: Checkerboard/background RGB for transparency.

    Returns:
        A new RGB PIL Image with the grid.
    """
    if layers is None or len(layers) == 0:
        return Image.new("RGB", (cell, cell), background)

    n = len(layers)
    cols = max(1, cols)
    rows = (n + cols - 1) // cols
    pad = 8
    label_h = 24
    cw = cell + pad * 2
    ch = cell + pad * 2 + label_h
    grid_w = cw * cols
    grid_h = ch * rows

    grid = Image.new("RGB", (grid_w, grid_h), (24, 24, 24))
    draw = ImageDraw.Draw(grid)
    try:
        font = ImageFont.load_default()
    except Exception:  # pragma: no cover
        font = None

    # Checkerboard background pattern for transparent layers
    checker = Image.new("RGB", (cell, cell), background)
    cd = ImageDraw.Draw(checker)
    cs = 16
    for yy in range(0, cell, cs):
        for xx in range(0, cell, cs):
            if ((xx // cs) + (yy // cs)) % 2 == 0:
                cd.rectangle([xx, yy, xx + cs - 1, yy + cs - 1], fill=(54, 54, 54))

    for idx, layer in enumerate(layers):
        r, c = divmod(idx, cols)
        ox = c * cw + pad
        oy = r * ch + pad
        # Cell background
        grid.paste(checker, (ox, oy))
        # Fit layer into cell
        fit = layer.convert("RGBA").copy()
        fit.thumbnail((cell, cell), Image.LANCZOS)
        fx = ox + (cell - fit.size[0]) // 2
        fy = oy + (cell - fit.size[1]) // 2
        grid.paste(fit, (fx, fy), fit)
        # Label
        label = labels[idx] if labels is not None and idx < len(labels) else f"layer_{idx:03d}"
        tx = ox
        ty = oy + cell + 4
        draw.text((tx, ty), label, fill=(235, 235, 235), font=font)

    return grid


def alpha_to_mask(img: Image.Image) -> np.ndarray:
    """Extract a binary mask from an image's alpha channel.

    Args:
        img: PIL Image (any mode with alpha, or it will be converted).

    Returns:
        Boolean numpy array of shape (H, W) where True = opaque (alpha > 0).
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img)
    return arr[:, :, 3] > 0
