#!/usr/bin/env python3
"""Pack RGBA layers into one or more texture atlases and bake them.

Supports:
- Shelf-packing (simple next-fit) and skyline-packing (more efficient)
- Multi-page atlases for large character sets
- NPOT (non-power-of-two) output
- Baking layers into atlas images based on pre-computed UV layouts
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger

log = get_logger("exporter.atlas")

Rect = Tuple[int, int, int, int]


class TextureAtlas:
    """Texture atlas packer with shelf/skyline algorithms and baking."""

    def __init__(
        self,
        max_size: int = 2048,
        padding: int = 2,
        algorithm: str = "skyline",
        power_of_two: bool = True,
    ) -> None:
        self.max_size = max(256, max_size)
        self.padding = max(0, padding)
        self.algorithm = algorithm if algorithm in ("shelf", "skyline") else "skyline"
        self.power_of_two = power_of_two
        self._placements: List[Dict[str, Rect]] = []
        self._uvs: Dict[str, Dict] = {}

    # ------------------------------------------------------------------
    # Packing (backward-compatible with the original interface)
    # ------------------------------------------------------------------

    def pack(self, layers: Dict[str, Image.Image]) -> Dict[str, Any]:
        """Pack layer images into atlas pages and return images + UVs.

        Returns:
            dict with ``atlases`` (list of PIL Images), ``uvs`` (per-layer
            placement info), and ``pages`` (raw rect dicts).
        """
        if not layers:
            empty = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            return {"atlases": [empty], "uvs": {}, "pages": []}

        # Build rectangle list
        rects: List[Tuple[str, int, int]] = []
        for name, img in layers.items():
            w, h = img.width, img.height
            if w <= 0 or h <= 0:
                continue
            if w > self.max_size or h > self.max_size:
                raise RuntimeError(
                    f"Layer {name} ({w}x{h}) exceeds max atlas size {self.max_size}"
                )
            rects.append((name, w + self.padding, h + self.padding))

        rects.sort(key=lambda r: (r[2], r[1]), reverse=True)

        pages: List[Dict[str, Rect]] = []
        remaining = list(rects)
        while remaining:
            if self.algorithm == "skyline":
                placements, leftover = self._skyline_pack(remaining)
            else:
                placements, leftover = self._shelf_pack(remaining)
            if not placements:
                log.error(f"Atlas packing failed for {len(remaining)} rects")
                break
            pages.append(placements)
            remaining = leftover

        self._placements = pages

        # Render atlas images
        atlases: List[Image.Image] = []
        uvs: Dict[str, Dict] = {}

        for page_idx, placements in enumerate(pages):
            if not placements:
                continue
            page_w = 0
            page_h = 0
            for (rx, ry, rw, rh) in placements.values():
                page_w = max(page_w, rx + rw)
                page_h = max(page_h, ry + rh)

            # Power-of-two rounding
            if self.power_of_two:
                page_w = self._next_po2(page_w)
                page_h = self._next_po2(page_h)
            else:
                page_w = min(self.max_size, page_w)
                page_h = min(self.max_size, page_h)

            atlas = Image.new("RGBA", (page_w, page_h), (0, 0, 0, 0))
            for name, img in layers.items():
                if name not in placements:
                    continue
                x, y, w, h = placements[name]
                w -= self.padding
                h -= self.padding
                if w <= 0 or h <= 0:
                    continue
                resized = img.resize((w, h), Image.LANCZOS) if (w, h) != img.size else img
                atlas.paste(resized, (x, y), resized)
                uvs[name] = {
                    "page": page_idx,
                    "top_left": (float(x), float(y)),
                    "bottom_right": (float(x + w), float(y + h)),
                    "u0": x / page_w,
                    "v0": y / page_h,
                    "u1": (x + w) / page_w,
                    "v1": (y + h) / page_h,
                    "atlas_width": page_w,
                    "atlas_height": page_h,
                }
            atlases.append(atlas)

        self._uvs = uvs
        log.info(f"Packed {len(uvs)} layers into {len(atlases)} atlas page(s)")
        return {"atlases": atlases, "uvs": uvs, "pages": pages}

    def bake_atlas(
        self,
        layers: Dict[str, Image.Image],
        meshes: Dict[str, Dict],
        uv_data: Dict[str, Dict],
        output_path: str,
    ) -> List[str]:
        """Bake layers into atlas PNG images using pre-computed UV data.

        This is separate from ``pack()`` — it accepts externally computed
        UV coordinates (e.g. from :class:`UVUnwrapper`) and writes the
        resulting atlas images.

        Args:
            layers:      Name -> PIL Image mapping.
            meshes:      Name -> mesh dict (unused directly, kept for interface symmetry).
            uv_data:     Name -> UV dict (from UVUnwrapper).
            output_path: Base path; page index and ``.png`` are appended.

        Returns:
            List of file paths written.
        """
        from pathlib import Path

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Determine atlas size from UV data
        atlas_size = self.max_size
        for info in uv_data.values():
            if "atlas_width" in info:
                atlas_size = max(atlas_size, info["atlas_width"])
                break

        # Group placements by page
        pages: Dict[int, Dict[str, Dict]] = {}
        for name, info in uv_data.items():
            pg = info.get("page", 0)
            pages.setdefault(pg, {})[name] = info

        written: List[str] = []
        for page_idx in sorted(pages.keys()):
            page_placements = pages[page_idx]
            atlas_w = page_placements[next(iter(page_placements))].get("atlas_width", atlas_size)
            atlas_h = page_placements[next(iter(page_placements))].get("atlas_height", atlas_size)

            canvas = Image.new("RGBA", (int(atlas_w), int(atlas_h)), (0, 0, 0, 0))
            for name, info in page_placements.items():
                if name not in layers:
                    continue
                img = layers[name]
                x1, y1 = info.get("top_left", (0, 0))
                x2, y2 = info.get("bottom_right", (img.width, img.height))
                tw, th = int(x2 - x1), int(y2 - y1)
                if tw <= 0 or th <= 0:
                    continue
                resized = img.resize((tw, th), Image.LANCZOS) if (tw, th) != img.size else img
                canvas.paste(resized, (int(x1), int(y1)), resized)

            stem = out.stem
            suffix = out.suffix or ".png"
            page_path = out.parent / f"{stem}_{page_idx:02d}{suffix}"
            canvas.save(str(page_path))
            written.append(str(page_path))
            log.info(f"Baked atlas page {page_idx} -> {page_path}")

        return written

    def get_uvs(self) -> Dict[str, Dict]:
        """Return UV coordinate data from the last ``pack()`` call."""
        return dict(self._uvs)

    # ------------------------------------------------------------------
    # Shelf packing
    # ------------------------------------------------------------------

    def _shelf_pack(
        self, rects: List[Tuple[str, int, int]]
    ) -> Tuple[Dict[str, Rect], List[Tuple[str, int, int]]]:
        placements: Dict[str, Rect] = {}
        leftover: List[Tuple[str, int, int]] = []
        shelf_y = 0
        shelf_x = 0
        shelf_height = 0
        S = self.max_size

        for name, w, h in rects:
            if shelf_x + w > S:
                shelf_y += shelf_height
                shelf_x = 0
                shelf_height = 0
            if shelf_y + h > S:
                leftover.append((name, w, h))
                continue
            placements[name] = (shelf_x, shelf_y, w, h)
            shelf_x += w
            shelf_height = max(shelf_height, h)
        return placements, leftover

    # ------------------------------------------------------------------
    # Skyline packing
    # ------------------------------------------------------------------

    def _skyline_pack(
        self, rects: List[Tuple[str, int, int]]
    ) -> Tuple[Dict[str, Rect], List[Tuple[str, int, int]]]:
        placements: Dict[str, Rect] = {}
        leftover: List[Tuple[str, int, int]] = []
        S = self.max_size
        skyline: List[List[int]] = [[0, 0]]

        for name, w, h in rects:
            best_x, best_y, _ = self._find_skyline_pos(skyline, w, h, S)
            if best_x is None:
                leftover.append((name, w, h))
                continue
            placements[name] = (best_x, best_y, w, h)
            self._add_skyline_rect(skyline, best_x, best_y, w, h)

        return placements, leftover

    @staticmethod
    def _find_skyline_pos(
        skyline: List[List[int]],
        w: int,
        h: int,
        S: int,
    ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        best_x: Optional[int] = None
        best_y: Optional[int] = None
        best_h = float("inf")
        best_idx: Optional[int] = None

        for i in range(len(skyline)):
            x_pos = skyline[i][0]
            if x_pos + w > S:
                break
            y_pos = 0
            for j in range(i, len(skyline)):
                if skyline[j][0] >= x_pos + w:
                    break
                y_pos = max(y_pos, skyline[j][1])
            if y_pos + h > S:
                continue
            if y_pos < best_h:
                best_h = y_pos
                best_x = x_pos
                best_y = y_pos
                best_idx = i

        return best_x, best_y, best_idx

    @staticmethod
    def _add_skyline_rect(
        skyline: List[List[int]],
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> None:
        top = y + h
        x_end = x + w

        # Raise segments within the rect
        for i in range(len(skyline)):
            sx = skyline[i][0]
            if sx >= x_end:
                break
            nx = skyline[i + 1][0] if i + 1 < len(skyline) else 10**9
            if nx <= x:
                continue
            if sx == x:
                skyline[i][1] = max(skyline[i][1], top)
            elif sx < x:
                skyline.insert(i + 1, [x, top])
            elif x < sx < x_end:
                skyline[i][1] = max(skyline[i][1], top)

        if not skyline or skyline[-1][0] < x_end:
            skyline.append([x_end, y])

        # Merge equal-height neighbours
        merged: List[List[int]] = []
        for pt in skyline:
            if merged and merged[-1][1] == pt[1]:
                continue
            merged.append([pt[0], pt[1]])
        skyline.clear()
        skyline.extend(merged)

    # ------------------------------------------------------------------
    # Util
    # ------------------------------------------------------------------

    @staticmethod
    def _next_po2(n: int) -> int:
        """Return the smallest power of two >= n."""
        if n <= 1:
            return 1
        return 1 << (n - 1).bit_length()
