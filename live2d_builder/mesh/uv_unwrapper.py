#!/usr/bin/env python3
"""UV coordinate management and texture atlas packing for Live2D meshes.

Provides shelf-packing and skyline-packing algorithms to pack rectangular
layer regions into power-of-two (or arbitrary) texture atlases, and
computes UV coordinates for each mesh.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from core.logger import get_logger

log = get_logger("rigging.uv")

# Type alias for a packed rectangle: (x, y, w, h)
Rect = Tuple[int, int, int, int]


class UVUnwrapper:
    """Pack meshes into a texture atlas and compute UV coordinates.

    Args:
        atlas_size: Maximum atlas dimension in pixels (square).
        padding:   Number of transparent pixels to add around each packed
                   rectangle to prevent texture bleeding.
        algorithm: ``"shelf"`` or ``"skyline"`` packing strategy.
    """

    def __init__(
        self,
        atlas_size: int = 2048,
        padding: int = 2,
        algorithm: str = "skyline",
    ) -> None:
        self.atlas_size = max(64, atlas_size)
        self.padding = max(0, padding)
        self.algorithm = algorithm if algorithm in ("shelf", "skyline") else "skyline"
        self._pages: List[Dict[str, Rect]] = []
        self._uv_data: Dict[str, Dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pack_meshes(self, meshes: Dict[str, Dict]) -> Dict:
        """Pack all meshes into one or more atlas pages.

        Args:
            meshes: dict of ``{layer_name: mesh_dict}`` where each mesh
                    contains ``width`` and ``height`` keys.

        Returns:
            dict with keys:
                - ``pages``: list of page placement dicts
                - ``uvs``: dict of UV coordinate dicts per mesh
                - ``atlas_size``: (w, h) per page (all same size)
        """
        if not meshes:
            self._pages = []
            self._uv_data = {}
            return {"pages": [], "uvs": {}, "atlas_size": (self.atlas_size, self.atlas_size)}

        # Build rectangle list sorted by height descending (tallest first)
        rects: List[Tuple[str, int, int]] = []
        for name, mesh in meshes.items():
            w = int(mesh.get("width", 0))
            h = int(mesh.get("height", 0))
            if w <= 0 or h <= 0:
                log.debug(f"Skipping mesh {name} with zero dimensions ({w}x{h})")
                continue
            if w > self.atlas_size or h > self.atlas_size:
                log.warning(f"Layer {name} ({w}x{h}) exceeds atlas size {self.atlas_size}; skipping")
                continue
            rects.append((name, w + self.padding, h + self.padding))

        # Sort by height descending, then width descending
        rects.sort(key=lambda r: (r[2], r[1]), reverse=True)

        pages: List[Dict[str, Rect]] = []
        remaining: List[Tuple[str, int, int]] = list(rects)

        while remaining:
            if self.algorithm == "skyline":
                placements, leftover = self._skyline_pack(remaining)
            else:
                placements, leftover = self._shelf_pack(remaining)
            if not placements:
                # Should not happen because we checked sizes, but guard anyway
                log.error(f"Packing failed for {len(remaining)} rects; giving up")
                break
            pages.append(placements)
            remaining = leftover

        self._pages = pages

        # Build UV data
        uvs: Dict[str, Dict] = {}
        for page_idx, placements in enumerate(pages):
            for name, (x, y, w, h) in placements.items():
                # Remove padding for actual content bounds
                px = self.padding
                uvs[name] = {
                    "page": page_idx,
                    # Pixel coordinates within atlas
                    "rect_px": (x, y, w - px, h - px),
                    # UV coordinates (0-1) — top-left origin (Live2D style)
                    "u0": x / self.atlas_size,
                    "v0": y / self.atlas_size,
                    "u1": (x + w - px) / self.atlas_size,
                    "v1": (y + h - px) / self.atlas_size,
                    "atlas_width": self.atlas_size,
                    "atlas_height": self.atlas_size,
                }
        self._uv_data = uvs

        log.info(f"Packed {len(uvs)} layers into {len(pages)} atlas page(s)")
        return {
            "pages": pages,
            "uvs": uvs,
            "atlas_size": (self.atlas_size, self.atlas_size),
        }

    def get_atlas_layout(self) -> Dict:
        """Return layout information for texture baking.

        Returns:
            dict with ``pages``, ``atlas_size``, ``padding``, ``counts``.
        """
        total_rects = sum(len(p) for p in self._pages)
        total_area = sum(
            (w * h)
            for page in self._pages
            for (_, _, w, h) in page.values()
        )
        atlas_area = self.atlas_size * self.atlas_size * max(1, len(self._pages))
        utilisation = total_area / atlas_area if atlas_area > 0 else 0.0

        return {
            "pages": self._pages,
            "uvs": self._uv_data,
            "atlas_size": (self.atlas_size, self.atlas_size),
            "padding": self.padding,
            "algorithm": self.algorithm,
            "counts": {
                "pages": len(self._pages),
                "rects": total_rects,
                "total_packed_area": total_area,
                "utilisation": round(utilisation, 4),
            },
        }

    def export_uv_layout_image(self, output_path: str) -> str:
        """Write a debug image visualising the UV atlas layout.

        Each packed rectangle is drawn as a coloured outline with its
        layer name labelled inside.

        Args:
            output_path: File path to write the PNG image.

        Returns:
            The absolute path written.
        """
        if not self._pages:
            log.warning("No packed pages; writing empty UV layout image")

        cols = max(1, min(4, len(self._pages)))
        rows = max(1, math.ceil(len(self._pages) / cols))
        label_h = 24
        total_w = cols * self.atlas_size
        total_h = rows * (self.atlas_size + label_h)
        canvas = Image.new("RGB", (total_w, total_h), (30, 30, 30))
        draw = ImageDraw.Draw(canvas)

        palette = [
            (231, 76, 60), (46, 204, 113), (52, 152, 219),
            (241, 196, 15), (155, 89, 182), (26, 188, 156),
            (230, 126, 34), (149, 165, 166),
        ]

        for page_idx, placements in enumerate(self._pages):
            col = page_idx % cols
            row = page_idx // cols
            ox = col * self.atlas_size
            oy = row * (self.atlas_size + label_h)

            # Page label
            draw.rectangle(
                [ox, oy, ox + self.atlas_size, oy + label_h],
                fill=(50, 50, 50),
            )
            draw.text(
                (ox + 8, oy + 6),
                f"Page {page_idx}  ({len(placements)} regions)",
                fill=(220, 220, 220),
            )

            ay = oy + label_h
            for i, (name, (x, y, w, h)) in enumerate(placements.items()):
                colour = palette[i % len(palette)]
                draw.rectangle(
                    [ox + x, ay + y, ox + x + w, ay + y + h],
                    outline=colour,
                    width=2,
                )
                # Truncate name to fit
                label = name[:18] + "..." if len(name) > 20 else name
                draw.text((ox + x + 3, ay + y + 3), label, fill=colour)

        canvas.save(output_path)
        log.info(f"UV layout image written to {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # Shelf packing (simple next-fit decreasing height)
    # ------------------------------------------------------------------

    def _shelf_pack(
        self, rects: List[Tuple[str, int, int]]
    ) -> Tuple[Dict[str, Rect], List[Tuple[str, int, int]]]:
        """Pack rectangles into a single page using shelf algorithm.

        Returns:
            Tuple of (placements dict, leftover rects that didn't fit).
        """
        placements: Dict[str, Rect] = {}
        leftover: List[Tuple[str, int, int]] = []

        shelf_y = 0
        shelf_x = 0
        shelf_height = 0
        S = self.atlas_size

        for name, w, h in rects:
            # Wrap to new shelf when current shelf is full
            if shelf_x + w > S:
                shelf_y += shelf_height
                shelf_x = 0
                shelf_height = 0

            # Start new page when out of vertical space
            if shelf_y + h > S:
                leftover.append((name, w, h))
                continue

            placements[name] = (shelf_x, shelf_y, w, h)
            shelf_x += w
            shelf_height = max(shelf_height, h)

        return placements, leftover

    # ------------------------------------------------------------------
    # Skyline packing (bottom-left skyline)
    # ------------------------------------------------------------------

    def _skyline_pack(
        self, rects: List[Tuple[str, int, int]]
    ) -> Tuple[Dict[str, Rect], List[Tuple[str, int, int]]]:
        """Pack rectangles into a single page using a skyline algorithm.

        The skyline is a list of ``(x, y)`` points defining the top edge
        of already-placed rectangles. For each new rectangle we find the
        lowest horizontal segment where it fits and place it there.

        Returns:
            Tuple of (placements dict, leftover rects that didn't fit).
        """
        placements: Dict[str, Rect] = {}
        leftover: List[Tuple[str, int, int]] = []

        S = self.atlas_size
        # Skyline: list of (x_position, height_at_that_position)
        skyline: List[List[int]] = [[0, 0]]

        for name, w, h in rects:
            best_x, best_y, best_idx = self._find_skyline_position(skyline, w, h, S)
            if best_x is None:
                leftover.append((name, w, h))
                continue
            placements[name] = (best_x, best_y, w, h)
            self._add_skyline_rect(skyline, best_x, best_y, w, h)

        return placements, leftover

    def _find_skyline_position(
        self,
        skyline: List[List[int]],
        w: int,
        h: int,
        S: int,
    ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """Find the best position on the skyline for a rectangle (w, h).

        Returns (x, y, skyline_segment_index) or (None, None, None).
        """
        best_x: Optional[int] = None
        best_y: Optional[int] = None
        best_idx: Optional[int] = None
        best_height = float("inf")

        for i in range(len(skyline)):
            x_pos = skyline[i][0]
            if x_pos + w > S:
                break

            # Find maximum height across the span [x_pos, x_pos + w]
            y_pos = 0
            for j in range(i, len(skyline)):
                if skyline[j][0] >= x_pos + w:
                    break
                y_pos = max(y_pos, skyline[j][1])

            if y_pos + h > S:
                continue

            # Prefer the placement with the lowest top edge, then leftmost
            if y_pos < best_height:
                best_height = y_pos
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
        """Insert a placed rectangle into the skyline, merging flat segments."""
        new_x = x
        new_top = y + h
        x_end = x + w

        # Find segments overlapping the new rect
        i = 0
        while i < len(skyline):
            sx, sy = skyline[i]
            if sx >= x_end:
                break
            if sx + (skyline[i + 1][0] if i + 1 < len(skyline) else 10**9) <= x:
                i += 1
                continue

            if sx == x:
                # Replace segment start
                skyline[i][1] = max(sy, new_top)
            elif sx < x:
                # Segment starts before x; insert a new point at x
                skyline.insert(i + 1, [x, new_top])
                i += 1
            elif sx > x and sx < x_end:
                # Interior segment gets raised
                skyline[i][1] = max(sy, new_top)
            i += 1

        # Append end point if past last segment
        if not skyline or skyline[-1][0] < x_end:
            skyline.append([x_end, y])

        # Merge adjacent segments with the same height
        merged: List[List[int]] = []
        for pt in skyline:
            if merged and merged[-1][1] == pt[1]:
                continue
            merged.append([pt[0], pt[1]])
        skyline.clear()
        skyline.extend(merged)
