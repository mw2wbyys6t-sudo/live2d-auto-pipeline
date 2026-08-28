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


def _remove_background_corner(img: Image.Image) -> Image.Image:
    """Corner-color-based background removal using a color-distance ramp.

    Samples the four corner patches, takes the median background color, and
    pushes pixels near that color toward transparency.
    """
    arr = np.array(img.convert("RGBA"))
    h, w = arr.shape[:2]
    patch = max(1, min(20, h // 20, w // 20))
    corners = np.vstack([
        arr[:patch, :patch, :3].reshape(-1, 3),
        arr[:patch, -patch:, :3].reshape(-1, 3),
        arr[-patch:, :patch, :3].reshape(-1, 3),
        arr[-patch:, -patch:, :3].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)
    rgb = arr[:, :, :3].astype(np.float32)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    # Smooth ramp between inner and outer thresholds
    inner, outer = 25.0, 50.0
    alpha = np.clip(255.0 * (dist - inner) / max(1.0, outer - inner), 0, 255)
    alpha = np.where(dist < inner, 0.0, np.where(dist > outer, 255.0, alpha))
    arr[:, :, 3] = np.minimum(arr[:, :, 3], alpha.astype(np.uint8))
    return Image.fromarray(arr, "RGBA")


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
