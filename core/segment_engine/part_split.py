#!/usr/bin/env python3
"""
Live2D Master Agent - Left/Right Part Splitting

Semantic segmenters detect limbs as one coarse mask (``arms``, ``hands``,
``legs``). Live2D rigging needs each side as an independent layer, because a
left arm and a right arm deform under different parameters.

Splitting by a text prompt such as "left arm" is unreliable: open-vocabulary
detectors confuse the two sides and the character's left is the viewer's right.
This module therefore splits an already-detected mask along the character's
body midline, which is estimated from the face/neck box.

Side convention: ``_L`` is the **character's** left, which appears on the
viewer's right for a front-facing character (larger x).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

#: Coarse mask name -> canonical base name used for the split layers.
SIDED_SOURCE_PARTS: Dict[str, str] = {
    "arms": "arm",
    "arm": "arm",
    "hands": "hand",
    "hand": "hand",
    "legs": "leg",
    "leg": "leg",
    "sleeves": "sleeve",
    "sleeve": "sleeve",
    "socks": "sock",
    "sock": "sock",
    "boots": "boot",
    "boot": "boot",
    "eyebrows": "eyebrow",
    "eyes": "eye_white",
}

#: Mask names whose bounding box is used to estimate the body midline, in
#: order of preference. The face is the most reliable anchor.
_MIDLINE_ANCHORS: Tuple[str, ...] = ("face", "head", "neck", "clothes_top")

#: A side smaller than this fraction of the parent mask is treated as noise.
_MIN_SIDE_RATIO = 0.02


def _bbox_center_x(mask: np.ndarray) -> Optional[float]:
    cols = np.where(mask.any(axis=0))[0]
    if cols.size == 0:
        return None
    return float(cols.min() + cols.max()) / 2.0


def estimate_midline(part_masks: Dict[str, np.ndarray], width: int) -> float:
    """Estimate the vertical body midline in pixel coordinates."""
    for anchor in _MIDLINE_ANCHORS:
        mask = part_masks.get(anchor)
        if mask is None:
            continue
        mask = np.asarray(mask, dtype=bool)
        center = _bbox_center_x(mask)
        if center is not None and 0 <= center <= width:
            return center

    union = np.zeros((1, width), dtype=bool)
    for mask in part_masks.values():
        mask = np.asarray(mask, dtype=bool)
        if mask.ndim == 2:
            union = np.zeros(mask.shape, dtype=bool)
            for other in part_masks.values():
                other = np.asarray(other, dtype=bool)
                if other.shape == mask.shape:
                    union |= other
            center = _bbox_center_x(union)
            if center is not None:
                return center
            break

    return width / 2.0


def split_mask_sides(
    mask: np.ndarray,
    midline: float,
    min_side_ratio: float = _MIN_SIDE_RATIO,
) -> Dict[str, np.ndarray]:
    """Split one mask into character-left / character-right parts."""
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return {}

    width = mask.shape[1]
    xs = np.arange(width)[None, :]
    character_left = mask & (xs >= midline)
    character_right = mask & (xs < midline)

    total = int(mask.sum())
    minimum = max(1, int(total * min_side_ratio))

    sides: Dict[str, np.ndarray] = {}
    if int(character_left.sum()) >= minimum:
        sides["L"] = character_left
    if int(character_right.sum()) >= minimum:
        sides["R"] = character_right
    return sides


def split_limb_parts(
    part_masks: Dict[str, np.ndarray],
    size: Tuple[int, int],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Replace coarse limb masks with per-side masks.

    Args:
        part_masks: mapping of part name -> boolean mask.
        size: ``(width, height)`` of the canvas.

    Returns:
        ``(new_masks, warnings)``. Coarse keys that were split are removed and
        replaced by ``<base>_L`` / ``<base>_R`` entries.
    """
    width = int(size[0])
    midline = estimate_midline(part_masks, width)

    result: Dict[str, np.ndarray] = {}
    warnings: List[str] = []

    for name, mask in part_masks.items():
        base = SIDED_SOURCE_PARTS.get(str(name))
        if base is None:
            result[name] = mask
            continue

        sides = split_mask_sides(mask, midline)
        if not sides:
            result[name] = mask
            warnings.append(f"{name} 未能按中线拆分，保留整块")
            continue

        for side, side_mask in sides.items():
            result[f"{base}_{side}"] = side_mask

        if len(sides) < 2:
            warnings.append(f"{name} 只检测到单侧（{next(iter(sides))}），另一侧缺失")

    return result, warnings


def is_sided_name(name: str) -> bool:
    lowered = str(name).lower()
    return any(lowered.endswith(suffix) for suffix in ("_l", "_r", "_left", "_right"))


def sided_bases(names: Iterable[str]) -> List[str]:
    bases: List[str] = []
    for name in names:
        lowered = str(name).lower()
        for suffix in ("_left", "_right", "_l", "_r"):
            if lowered.endswith(suffix):
                bases.append(lowered[: -len(suffix)])
                break
    return bases
