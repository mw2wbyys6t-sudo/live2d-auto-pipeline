#!/usr/bin/env python3
"""
Pygmalion Studio - Left/Right Part Splitting

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
    "eyewhite": "eye_white",
    "irides": "eye_iris",
    "eyelash": "eyelash",
    "ears": "ear",
    "earwear": "earwear",
    "handwear": "handwear",
    "legwear": "sock",
    "footwear": "boot",
}

#: Mask names whose bounding box is used to estimate the body midline, in
#: order of preference. The face is the most reliable anchor.
_MIDLINE_ANCHORS: Tuple[str, ...] = ("face", "head", "neck", "clothes_top")

#: A side smaller than this fraction of the parent mask is treated as noise.
_MIN_SIDE_RATIO = 0.02

#: Edge-band ratios, as a fraction of the parent part's height. These are the
#: tuning knobs for derived garment pieces: smaller values give thinner bands.
CUFF_BAND_RATIO = 0.10
SKIRT_HEM_BAND_RATIO = 0.08

#: Minimum band height in pixels so small parts still produce a usable strip.
MIN_BAND_PX = 4

#: Garment pieces whose edge bands are promoted to their own layers.
#: ``name -> ((derived base, edge, band ratio of the parent height), ...)``
#: Multiple bands may be cut from one parent; they are taken in order from the
#: remaining mask, so later cuts never overlap earlier ones.
_BAND_DERIVATIONS: Dict[str, Tuple[Tuple[str, str, float], ...]] = {
    "arm_L": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "arm_R": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "sleeve_L": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "sleeve_R": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "skirt": (("skirt_hem", "bottom", SKIRT_HEM_BAND_RATIO), ("waistband", "top", 0.08)),
    "skirt_body": (("skirt_hem", "bottom", SKIRT_HEM_BAND_RATIO), ("waistband", "top", 0.08)),
    "clothes_bottom": (("skirt_hem", "bottom", SKIRT_HEM_BAND_RATIO), ("waistband", "top", 0.08)),
    "clothes_top": (("collar", "top", 0.10),),
    "outer_body": (("collar", "top", 0.10),),
    "shirt": (("collar", "top", 0.10),),
    "boot_L": (("boot_cuff", "top", 0.15),),
    "boot_R": (("boot_cuff", "top", 0.15),),
}

#: Parts produced by geometric edge bands rather than by detection.
DERIVED_PARTS = frozenset({"cuff", "skirt_hem", "collar", "waistband", "boot_cuff"})

#: Eye masks that can be split into sclera (white) and iris by luminance.
EYE_SOURCES: Dict[str, str] = {
    "eyes_left": "left",
    "eyes_right": "right",
    "eye_left": "left",
    "eye_right": "right",
    "eye_white_L": "left",
    "eye_white_R": "right",
    "eye_white_left": "left",
    "eye_white_right": "right",
}

#: Hair masks that must end up split into front/back.
HAIR_FRONT = "hair_front"
HAIR_BACK = "hair_back"


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


def _component_labels(mask: np.ndarray):
    """Return a label image, preferring cv2 then scipy; None when unavailable."""
    try:
        import cv2  # type: ignore

        count, labels = cv2.connectedComponents(mask.astype(np.uint8))
        return labels if count > 1 else None
    except Exception:
        pass
    try:
        from scipy import ndimage  # type: ignore

        labels, count = ndimage.label(mask)
        return labels if count > 1 else None
    except Exception:
        return None


def _split_by_components(
    mask: np.ndarray,
    midline: float,
    minimum: int,
) -> Dict[str, np.ndarray]:
    """Assign each connected component to a side by its bbox centre.

    Components are never cut, so a limb drawn across the midline stays whole.
    """
    labels = _component_labels(mask)
    if labels is None:
        return {}

    left = np.zeros_like(mask, dtype=bool)
    right = np.zeros_like(mask, dtype=bool)
    found_left = found_right = False

    for label in range(1, int(labels.max()) + 1):
        component = labels == label
        if int(component.sum()) < minimum:
            continue
        cols = np.where(component.any(axis=0))[0]
        centre = float(cols.min() + cols.max()) / 2.0
        if centre >= midline:
            left |= component
            found_left = True
        else:
            right |= component
            found_right = True

    if not (found_left and found_right):
        return {}
    return {"L": left, "R": right}


def _split_by_column_gap(
    mask: np.ndarray,
    midline: float,
    minimum: int,
) -> Dict[str, np.ndarray]:
    """Split at the empty column closest to the body midline.

    Used when no labeling library is available. Only splits when a genuinely
    empty (or near-empty) column exists, so it cannot cut through a limb.
    """
    height, width = mask.shape
    columns = mask.sum(axis=0).astype(np.int64)
    occupied = np.where(columns > 0)[0]
    if occupied.size < 2:
        return {}

    left_edge, right_edge = int(occupied.min()), int(occupied.max())
    span = right_edge - left_edge
    if span < 8:
        return {}

    low = left_edge + max(1, int(span * 0.15))
    high = right_edge - max(1, int(span * 0.15))
    if high <= low:
        return {}

    best = low + int(np.argmin(columns[low : high + 1]))
    if int(columns[best]) > max(1, int(columns.max() * 0.05)):
        return {}

    xs = np.arange(width)[None, :]
    left = mask & (xs >= best)
    right = mask & (xs < best)
    if int(left.sum()) < minimum or int(right.sum()) < minimum:
        return {}
    return {"L": left, "R": right}


def split_mask_sides(
    mask: np.ndarray,
    midline: float,
    min_side_ratio: float = _MIN_SIDE_RATIO,
    use_components: bool = True,
) -> Dict[str, np.ndarray]:
    """Split one mask into character-left / character-right parts.

    Order of preference: connected components (keeps each limb whole), then an
    empty column gap, then a hard cut at the midline.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return {}

    total = int(mask.sum())
    minimum = max(1, int(total * min_side_ratio))

    if use_components:
        sides = _split_by_components(mask, midline, minimum)
        if sides:
            return sides

    sides = _split_by_column_gap(mask, midline, minimum)
    if sides:
        return sides

    width = mask.shape[1]
    xs = np.arange(width)[None, :]
    left = mask & (xs >= midline)
    right = mask & (xs < midline)

    result: Dict[str, np.ndarray] = {}
    if int(left.sum()) >= minimum:
        result["L"] = left
    if int(right.sum()) >= minimum:
        result["R"] = right
    return result


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


def split_eye_parts(
    part_masks: Dict[str, np.ndarray],
    image_rgb: np.ndarray,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Split each eye mask into sclera (``eyewhite_*``) and iris (``irides_*``).

    The split is luminance-based inside an already-detected eye mask, so no
    extra detector pass is needed. If the two clusters are not separable, the
    original eye mask is kept rather than inventing parts.
    """
    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []

    rgb = np.asarray(image_rgb)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return result, warnings

    luminance = (
        0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    ).astype(np.float32)

    for name, side in EYE_SOURCES.items():
        mask = result.pop(name, None)
        if mask is None:
            continue

        mask = np.asarray(mask, dtype=bool)
        if int(mask.sum()) < 30:
            result[name] = mask
            continue

        values = luminance[mask]
        low, high = np.percentile(values, 20), np.percentile(values, 80)
        if high - low < 10:
            result[name] = mask
            warnings.append(f"{name} 明暗差不足，未拆分眼白/瞳孔")
            continue

        threshold = (low + high) / 2.0
        iris = mask & (luminance <= threshold)
        sclera = mask & (luminance > threshold)
        iris_ratio = float(iris.sum()) / max(1, int(mask.sum()))
        if not iris.any() or not sclera.any() or not 0.08 <= iris_ratio <= 0.8:
            result[name] = mask
            warnings.append(f"{name} 眼白/瞳孔比例异常（{iris_ratio:.0%}），保留整块")
            continue

        result[f"irides_{side}"] = iris
        result[f"eyewhite_{side}"] = sclera

    return result, warnings


def split_hair_parts(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Ensure hair is split into distinct front/back masks.

    Some backends emit one hair mask and copy it into both slots, which draws
    the same pixels twice. When the two masks are identical (or one is
    missing), the hair mass is split against the face box: hair covering the
    face is front hair, the rest is back hair.
    """
    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []

    front = result.get(HAIR_FRONT)
    back = result.get(HAIR_BACK)
    if front is None and back is None:
        return result, warnings

    front_mask = np.asarray(front, dtype=bool) if front is not None else None
    back_mask = np.asarray(back, dtype=bool) if back is not None else None
    if front_mask is not None and back_mask is not None and not np.array_equal(front_mask, back_mask):
        return result, warnings

    shape = front_mask.shape if front_mask is not None else back_mask.shape
    hair = np.zeros(shape, dtype=bool)
    if front_mask is not None:
        hair |= front_mask
    if back_mask is not None:
        hair |= back_mask
    if not hair.any():
        return result, warnings

    face = result.get("face")
    face_mask = np.asarray(face, dtype=bool) if face is not None else None

    if face_mask is not None and face_mask.shape == shape and face_mask.any():
        new_front = hair & face_mask
        new_back = hair & ~face_mask
        source = "面部包围盒"
    else:
        rows = np.where(hair.any(axis=1))[0]
        top, bottom = int(rows.min()), int(rows.max())
        cut = top + max(1, int((bottom - top + 1) * 0.4))
        new_front = hair.copy()
        new_front[cut:, :] = False
        new_back = hair.copy()
        new_back[:cut, :] = False
        source = "上部 40%"

    if not new_front.any() or not new_back.any():
        warnings.append("头发前后拆分失败，保留原掩码")
        return result, warnings

    result[HAIR_FRONT] = new_front
    result[HAIR_BACK] = new_back
    warnings.append(f"头发前后由同一掩码按{source}拆分")
    return result, warnings


#: Parent masks that can be split into front/back by a depth map.
#: Larger depth = nearer to the viewer = front layer.
DEPTH_SPLIT_PARENTS: Dict[str, Tuple[str, str]] = {
    "hair": ("hair_front", "hair_back"),
    "hair_mid": ("hair_front", "hair_back"),
    "skirt": ("skirt_front", "skirt_back"),
    "skirt_body": ("skirt_front", "skirt_back"),
    "clothes_bottom": ("bottomwear_front", "bottomwear_back"),
}


def _normalize_depth(depth_map) -> Optional[np.ndarray]:
    depth = np.asarray(depth_map, dtype=np.float32)
    if depth.ndim != 2 or depth.size == 0:
        return None
    finite = depth[np.isfinite(depth)]
    if finite.size == 0:
        return None
    peak = float(np.nanmax(finite))
    if peak > 1.0:
        depth = depth / peak
    return depth


def split_depth_parts(
    part_masks: Dict[str, np.ndarray],
    depth_map,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Split selected parts into front/back using a per-pixel depth map.

    No depth map means no split: this function never invents depth values.
    """
    depth = _normalize_depth(depth_map)
    if depth is None:
        return part_masks, ["深度图不可用，跳过前后分层"]

    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []

    for parent, (front_name, back_name) in DEPTH_SPLIT_PARENTS.items():
        mask = result.get(parent)
        if mask is None:
            continue

        mask = np.asarray(mask, dtype=bool)
        if mask.shape != depth.shape:
            warnings.append(f"{parent} 与深度图尺寸不一致，跳过")
            continue
        total = int(mask.sum())
        if total < 100:
            continue

        values = depth[mask]
        low, high = np.percentile(values, 10), np.percentile(values, 90)
        if high - low < 0.02:
            warnings.append(f"{parent} 深度差不足，未拆前后")
            continue

        threshold = float(np.median(values))
        front = mask & (depth >= threshold)
        back = mask & (depth < threshold)
        if int(front.sum()) < total * 0.1 or int(back.sum()) < total * 0.1:
            warnings.append(f"{parent} 前后比例失衡，保留整块")
            continue

        result.pop(parent, None)
        result[front_name] = result.get(front_name, np.zeros_like(mask)) | front
        result[back_name] = result.get(back_name, np.zeros_like(mask)) | back
        warnings.append(f"{parent} 按深度拆为 {front_name}/{back_name}")

    return result, warnings


def attach_depths(
    layers: Dict[str, Dict],
    part_masks: Dict[str, np.ndarray],
    depth_map,
) -> Dict[str, Dict]:
    """Attach each layer's mean depth so ordering can use depth instead of z."""
    depth = _normalize_depth(depth_map)
    if depth is None:
        return layers

    for name, info in layers.items():
        mask = part_masks.get(name)
        if mask is None:
            continue
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != depth.shape or not mask.any():
            continue
        info["depth"] = round(float(depth[mask].mean()), 6)
    return layers


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
    band_height = max(MIN_BAND_PX, int(round(height * ratio)))
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
