#!/usr/bin/env python3
"""Pack RGBA layers into one or more texture atlases.

Supports:
- Shelf-packing (simple next-fit) and skyline-packing (more efficient)
- Multi-page atlases for large character sets
- NPOT (non-power-of-two) output
- 按 alpha 包围盒裁剪后再打包（整画布图层不再一层占一页）
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger

log = get_logger("exporter.atlas")

Rect = Tuple[int, int, int, int]


def content_box(img: Image.Image) -> Tuple[int, int, int, int]:
    """返回 ``(x0, y0, w, h)``：alpha 非零的紧包围盒。

    图层导出的是整张画布（4K 立绘每层都是 2731×4096 的透明大图）。按画布尺寸
    打包会让每层各占一整页 —— 实测 32 层 = 32 页 4096²，光贴图就 2GB。

    整层全透明时返回左上角 1×1：moc3 要求每个网格都有 UV 摆放信息，UV 落在
    一块全透明像素上即可，不能因为「看不见」就漏掉这一项。
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    box = img.getchannel("A").getbbox()
    if not box:
        return (0, 0, 1, 1)
    x0, y0, x1, y1 = box
    return (int(x0), int(y0), max(1, x1 - int(x0)), max(1, y1 - int(y0)))


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

        # 图层导出的是整张画布（4K 立绘每层都是 2731×4096 的透明大图）。按画布
        # 尺寸打包会让每层各占一整页 —— 实测 32 层 = 32 页 4096²，光贴图就
        # 2GB。先按 alpha 紧包围盒裁剪，同一批图层只需 1 页。裁剪偏移写进 UV
        # 的 ``bbox``，moc3 据此把画布坐标映射回裁剪后的 UV。
        crops: Dict[str, Tuple[int, int, int, int]] = {
            name: self._content_box(img) for name, img in layers.items()
        }

        padding = self.padding
        largest = max((max(b[2], b[3]) for b in crops.values()), default=0)
        if largest + padding * 2 > self.max_size:
            # 部件本身顶到图集上限（4K 画布高 4096）：padding 只能让路，
            # 否则 rect 比图集还大，一整批都放不进去，UV 全空。
            log.info(
                f"Atlas padding {padding} → 0 (largest part {largest}px, "
                f"max {self.max_size})"
            )
            padding = 0

        rects: List[Tuple[str, int, int]] = []
        for name, (bx, by, bw, bh) in crops.items():
            if bw > self.max_size or bh > self.max_size:
                raise RuntimeError(
                    f"Layer {name} ({bw}x{bh}) exceeds max atlas size {self.max_size}"
                )
            rects.append((name, bw + padding * 2, bh + padding * 2))

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
                bx, by, bw, bh = crops[name]
                rx, ry, packed_w, packed_h = placements[name]
                x, y = rx + padding, ry + padding
                w = packed_w - padding * 2
                h = packed_h - padding * 2
                if w <= 0 or h <= 0:
                    continue
                content = img.crop((bx, by, bx + bw, by + bh))
                resized = (
                    content.resize((w, h), Image.LANCZOS)
                    if (w, h) != content.size else content
                )
                atlas.paste(resized, (x, y), resized)
                if padding:
                    pixels = np.asarray(resized)
                    extended = np.pad(
                        pixels,
                        ((padding, padding), (padding, padding), (0, 0)),
                        mode="edge",
                    )
                    atlas.paste(Image.fromarray(extended, "RGBA"), (rx, ry))
                uvs[name] = {
                    "page": page_idx,
                    "top_left": (float(x), float(y)),
                    "bottom_right": (float(x + w), float(y + h)),
                    # 裁剪框（画布坐标）：moc3 用 (顶点 - bbox 原点) / bbox 尺寸
                    # 把画布坐标映射到这块 UV。
                    "bbox": (float(bx), float(by), float(bw), float(bh)),
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
    def _content_box(img: Image.Image) -> Tuple[int, int, int, int]:
        """兼容旧调用：见模块级 :func:`content_box`。"""
        return content_box(img)

    @staticmethod
    def _next_po2(n: int) -> int:
        """Return the smallest power of two >= n."""
        if n <= 1:
            return 1
        return 1 << (n - 1).bit_length()
