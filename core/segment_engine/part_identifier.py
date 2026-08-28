#!/usr/bin/env python3
"""
Live2D Master Agent - Part Identifier

Identifies body parts from K-means clusters (or other layer outputs) using
position (computed from actual pixel centroids), mean color, and area.
Each classified layer receives both a Chinese part name (``part_name``) and
an English part name (``part_name_en``) for downstream rigging.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger

log = get_logger("part_identifier")

# Reference color ranges for Live2D parts (in RGB). Each entry also carries
# an English label for downstream use.
PART_COLOR_RANGES: Dict[str, Dict] = {
    "头发":      {"en": "hair",          "rgb_range": [(20, 20, 20), (100, 60, 40)],   "y_range": (0.0, 0.45)},
    "头发_亮":   {"en": "hair_highlight","rgb_range": [(200, 180, 100), (255, 240, 180)],"y_range": (0.0, 0.5)},
    "头发_后":   {"en": "hair_back",     "rgb_range": [(20, 20, 20), (110, 70, 50)],   "y_range": (0.0, 0.55)},
    "头发_前":   {"en": "hair_front",    "rgb_range": [(20, 20, 20), (120, 80, 60)],   "y_range": (0.0, 0.45)},
    "皮肤":      {"en": "skin",          "rgb_range": [(220, 170, 150), (255, 220, 200)],"y_range": (0.2, 0.8)},
    "脸":        {"en": "face",          "rgb_range": [(230, 190, 170), (255, 230, 220)],"y_range": (0.1, 0.4)},
    "眼睛_白":   {"en": "eye_white",     "rgb_range": [(230, 230, 240), (255, 255, 255)],"y_range": (0.2, 0.35), "size_max": 0.05},
    "眼睛_瞳":   {"en": "eye_iris",      "rgb_range": [(20, 40, 80), (100, 120, 180)], "y_range": (0.22, 0.33), "size_max": 0.03},
    "眉毛":      {"en": "eyebrows",      "rgb_range": [(40, 30, 20), (120, 80, 60)],   "y_range": (0.18, 0.28)},
    "嘴巴":      {"en": "mouth",         "rgb_range": [(180, 60, 80), (255, 120, 140)],"y_range": (0.32, 0.4), "size_max": 0.03},
    "衣服":      {"en": "clothes",       "rgb_range": [(50, 80, 150), (200, 180, 220)],"y_range": (0.35, 0.9)},
    "衣服_暗":   {"en": "clothes_shadow","rgb_range": [(30, 50, 100), (100, 80, 120)], "y_range": (0.35, 0.9)},
    "腮红":      {"en": "blush",         "rgb_range": [(255, 150, 160), (255, 200, 200)],"y_range": (0.28, 0.38), "size_max": 0.02},
    "鼻子":      {"en": "nose",          "rgb_range": [(200, 140, 130), (240, 190, 180)],"y_range": (0.28, 0.36), "size_max": 0.01},
    "阴影":      {"en": "shadow",        "rgb_range": [(60, 50, 70), (130, 110, 130)], "y_range": (0.0, 1.0)},
    "未分类":    {"en": "unknown",       "rgb_range": [(0, 0, 0), (255, 255, 255)],    "y_range": (0.0, 1.0)},
}

# Reverse lookup: English -> Chinese (kept for compatibility)
EN_TO_CN: Dict[str, str] = {v["en"]: k for k, v in PART_COLOR_RANGES.items()}


class PartIdentifier:
    """Identifies body parts from layer color/position information."""

    def __init__(self) -> None:
        self.color_ranges: Dict[str, Dict] = PART_COLOR_RANGES

    # ------------------------------------------------------------------ public

    def identify_part(
        self,
        mean_color: Tuple[int, int, int],
        centroid_y_ratio: float,
        area_ratio: float = 1.0,
    ) -> str:
        """Identify a body part based on color, vertical position, and size.

        Args:
            mean_color: ``(R, G, B)`` tuple, 0-255.
            centroid_y_ratio: ``0.0`` (top) to ``1.0`` (bottom) vertical position.
            area_ratio: Fraction of total image area this layer occupies.

        Returns:
            Best matching Chinese part name (``"未分类"`` if no match).
        """
        best_part = "未分类"
        best_score = -1.0

        for part_name, criteria in self.color_ranges.items():
            if part_name == "未分类":
                continue
            score = self._score_part(mean_color, centroid_y_ratio, area_ratio, criteria)
            if score > best_score:
                best_score = score
                best_part = part_name

        return best_part if best_score > 0 else "未分类"

    def identify_part_en(
        self,
        mean_color: Tuple[int, int, int],
        centroid_y_ratio: float,
        area_ratio: float = 1.0,
    ) -> str:
        """Like :meth:`identify_part` but returns the English part name."""
        cn = self.identify_part(mean_color, centroid_y_ratio, area_ratio)
        return self.color_ranges.get(cn, self.color_ranges["未分类"])["en"]

    def identify_layers(
        self,
        layers_info: List[Dict],
        image_height: int,
        image_width: int,
        image: Optional[Image.Image] = None,
    ) -> List[Dict]:
        """Identify parts for a list of layer info dicts.

        Centroid position is computed from *actual pixel positions* whenever
        possible: if a layer dict carries a ``path`` to an exported PNG and
        the file exists, the alpha mask is loaded and used to compute the
        true centroid (and refine the mean color). If not, the method falls
        back to the previous area/color heuristic so that existing call sites
        (which only pass height/width) continue to work.

        Args:
            layers_info: List of layer dicts. Each should have at least
                ``color`` (RGB tuple) and ``pixel_count``. ``path`` to the
                layer PNG enables accurate centroid computation.
            image_height: Height of the source image (for fallback scaling).
            image_width: Width of the source image.
            image: Optional source PIL image. If provided and a layer has no
                ``path``, its label/color is used against ``image`` directly.

        Returns:
            The same list, with ``part_name`` (Chinese) and ``part_name_en``
            (English) keys added to each dict.
        """
        total_pixels = max(1, image_height * image_width)

        for layer in layers_info:
            color = tuple(layer.get("color", (128, 128, 128)))
            area_ratio = layer.get("pixel_count", 0) / total_pixels

            # If the layer already carries a semantic part_name from upstream
            # (e.g. SemanticSegmenter / LayerComposer), trust it and skip the
            # color-heuristic guess. We still compute centroid/mean_color
            # because downstream code (52-layer mapping, rigging) may use them.
            existing_pn = layer.get("part_name")
            existing_pn_en = layer.get("part_name_en")
            pre_identified = bool(existing_pn) and existing_pn not in ("未分类", "unknown", "", None)

            centroid_y, centroid_x = self._compute_centroid(
                layer, image, image_height, image_width, area_ratio, color
            )
            layer["centroid_y_ratio"] = float(centroid_y)
            layer["centroid_x_ratio"] = float(centroid_x)
            # Use the refined mean color if centroid computation produced one
            mean_color = layer.get("mean_color", color)

            if pre_identified:
                # Ensure part_name_en is populated
                if not existing_pn_en:
                    # Look up English name from color_ranges via matching Chinese key
                    for cn, crit in self.color_ranges.items():
                        if cn == existing_pn:
                            layer["part_name_en"] = crit.get("en", existing_pn)
                            break
                    else:
                        layer["part_name_en"] = existing_pn
                continue

            part_cn = self.identify_part(mean_color, centroid_y, area_ratio)
            part_en = self.color_ranges.get(part_cn, self.color_ranges["未分类"])["en"]
            layer["part_name"] = part_cn
            layer["part_name_en"] = part_en

        return layers_info

    # --------------------------------------------------------------- internal

    def _compute_centroid(
        self,
        layer: Dict,
        image: Optional[Image.Image],
        image_height: int,
        image_width: int,
        area_ratio: float,
        color: Tuple[int, int, int],
    ) -> Tuple[float, float]:
        """Return ``(cy_ratio, cx_ratio)`` for a layer, from pixel data if possible.

        Also updates ``layer["mean_color"]`` when pixel data is available.
        """
        path = layer.get("path")
        mask_arr: Optional[np.ndarray] = None
        rgb_arr: Optional[np.ndarray] = None

        if path and Path(path).is_file():
            try:
                with Image.open(path) as im:
                    arr = np.array(im.convert("RGBA"))
                mask_arr = arr[:, :, 3] > 0
                rgb_arr = arr[:, :, :3]
            except Exception as exc:
                log.debug(f"Could not read layer '{path}' for centroid: {exc}")
                mask_arr = None

        # If we have a label index but no path, try to derive from source image
        if mask_arr is None and image is not None and "label" in layer:
            try:
                src = np.array(image.convert("RGBA"))
                # Cannot know cluster membership without the label map here,
                # so fall through to heuristic.
                _ = src
            except Exception:
                pass

        if mask_arr is not None and mask_arr.any():
            h, w = mask_arr.shape
            ys, xs = np.where(mask_arr)
            cy = float(ys.mean()) / max(1, h - 1)
            cx = float(xs.mean()) / max(1, w - 1)
            if rgb_arr is not None:
                pixels = rgb_arr[mask_arr]
                layer["mean_color"] = tuple(int(c) for c in pixels.mean(axis=0))
            return cy, cx

        # Fallback heuristic (preserves previous behavior)
        if area_ratio > 0.2:
            y_ratio = 0.6  # likely body/clothes
        elif color[0] < 100 and color[1] < 80 and color[2] < 80:
            y_ratio = 0.2  # dark colors at top = hair
        else:
            y_ratio = 0.4  # face area default
        # x centroid defaults to center
        return y_ratio, 0.5

    def _score_part(
        self,
        color: Tuple[int, int, int],
        y_ratio: float,
        area_ratio: float,
        criteria: Dict,
    ) -> float:
        """Score how well a cluster matches a part definition."""
        score = 0.0

        # Color match (RGB distance to range center)
        rgb_min, rgb_max = criteria["rgb_range"]
        color_arr = np.array(color, dtype=np.float32)
        min_arr = np.array(rgb_min, dtype=np.float32)
        max_arr = np.array(rgb_max, dtype=np.float32)
        center = (min_arr + max_arr) / 2.0
        radius = float(np.linalg.norm(max_arr - min_arr)) / 2.0

        dist = float(np.linalg.norm(color_arr - center))
        if radius > 0 and dist < radius * 1.5:
            color_score = max(0.0, 1.0 - dist / (radius * 1.5))
            score += color_score * 0.6
        elif np.all(color_arr >= min_arr - 20) and np.all(color_arr <= max_arr + 20):
            score += 0.1
        else:
            return -1.0  # Color out of range

        # Position match
        y_min, y_max = criteria.get("y_range", (0.0, 1.0))
        if y_min <= y_ratio <= y_max:
            y_center = (y_min + y_max) / 2
            y_span = (y_max - y_min) / 2
            if y_span > 0:
                y_score = max(0.0, 1.0 - abs(y_ratio - y_center) / y_span)
                score += y_score * 0.3
        else:
            score -= 0.2

        # Size constraint (eyes/mouth should be small)
        size_max = criteria.get("size_max")
        if size_max is not None and area_ratio > size_max * 3:
            score -= 0.5

        return score


__all__ = ["PartIdentifier", "PART_COLOR_RANGES", "EN_TO_CN"]
