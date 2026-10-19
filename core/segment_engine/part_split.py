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

#: Garment pieces whose edge bands are promoted to their own layers.
#: ``name -> ((derived base, edge, band ratio of the parent height), ...)``
#: Multiple bands may be cut from one parent; they are taken in order from the
#: remaining mask, so later cuts never overlap earlier ones.
_BAND_DERIVATIONS: Dict[str, Tuple[Tuple[str, str, float], ...]] = {
    "arm_L": (("cuff", "bottom", 0.14),),
    "arm_R": (("cuff", "bottom", 0.14),),
    "sleeve_L": (("cuff", "bottom", 0.14),),
    "sleeve_R": (("cuff", "bottom", 0.14),),
    "skirt": (("skirt_hem", "bottom", 0.12), ("waistband", "top", 0.08)),
    "skirt_body": (("skirt_hem", "bottom", 0.12), ("waistband", "top", 0.08)),
    "clothes_bottom": (("skirt_hem", "bottom", 0.12), ("waistband", "top", 0.08)),
    "clothes_top": (("collar", "top", 0.10),),
    "outer_body": (("collar", "top", 0.10),),
    "shirt": (("collar", "top", 0.10),),
    "boot_L": (("boot_cuff", "top", 0.15),),
    "boot_R": (("boot_cuff", "top", 0.15),),
}

#: Parts produced by geometric edge bands rather than by detection.
DERIVED_PARTS = frozenset({"cuff", "skirt_hem", "collar", "waistband", "boot_cuff"})


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


def _side_suffix(name: str) -> Optional[str]:
    lowered = str(name).lower()
    for suffix, side in (("_left", "L"), ("_right", "R"), ("_l", "L"), ("_r", "R")):
        if lowered.endswith(suffix):
            return side
    return None


def _edge_band(mask: np.ndarray, edge: str, ratio: float) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Return ``(band, remainder)`` for the top or bottom band of a mask."""
    mask = np.asarray(mask, dtype=bool)
    rows = np.where(mask.any(axis=1))[0]
    if rows.size == 0:
        return None, None

    top, bottom = int(rows.min()), int(rows.max())
    height = bottom - top + 1
    band_height = max(2, int(round(height * ratio)))
    if band_height >= height:
        return None, None

    band = np.zeros_like(mask, dtype=bool)
    if edge == "bottom":
        band[bottom - band_height + 1 : bottom + 1, :] = mask[bottom - band_height + 1 : bottom + 1, :]
    else:
        band[top : top + band_height, :] = mask[top : top + band_height, :]

    return band, mask & ~band


def split_garment_details(
    part_masks: Dict[str, np.ndarray],
    size: Tuple[int, int],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Promote cuffs, skirt hem and collar into independent layers.

    These are geometric bands cut from an already-detected garment mask. They
    are honest *derived* pieces, not learned detections, and are marked as such
    in the manifest.
    """
    width = int(size[0])
    midline = estimate_midline(part_masks, width)

    result: Dict[str, np.ndarray] = {}
    derived: List[str] = []

    def _merge(name: str, mask: np.ndarray) -> None:
        if name in result:
            result[name] = result[name] | mask
        else:
            result[name] = mask

    for name, mask in part_masks.items():
        specs = _BAND_DERIVATIONS.get(str(name))
        if specs is None:
            _merge(str(name), np.asarray(mask, dtype=bool))
            continue

        side = _side_suffix(str(name))
        remaining = np.asarray(mask, dtype=bool)

        for base, edge, ratio in specs:
            band, remainder = _edge_band(remaining, edge, ratio)
            if band is None or not band.any() or remainder is None or not remainder.any():
                continue

            if base == "skirt_hem":
                halves = split_mask_sides(band, midline, min_side_ratio=0.25)
                if len(halves) == 2:
                    for half_side, half_mask in halves.items():
                        _merge(f"{base}_{half_side}", half_mask)
                        derived.append(f"{base}_{half_side}")
                    remaining = remainder
                    continue

            derived_name = f"{base}_{side}" if side else base
            _merge(derived_name, band)
            derived.append(derived_name)
            remaining = remainder

        _merge(str(name), remaining)

    return result, derived


def is_sided_name(name: str) -> bool:
    return _side_suffix(name) is not None


def sided_bases(names: Iterable[str]) -> List[str]:
    bases: List[str] = []
    for name in names:
        lowered = str(name).lower()
        for suffix in ("_left", "_right", "_l", "_r"):
            if lowered.endswith(suffix):
                bases.append(lowered[: -len(suffix)])
                break
    return bases
