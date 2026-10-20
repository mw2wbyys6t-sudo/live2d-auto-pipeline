#!/usr/bin/env python3
"""自适应像素格网（ArtMesh）——保证图层像素被网格完整覆盖。

为什么不直接复用 :class:`MeshGenerator` 的 Delaunay 实现：
那条路径是「轮廓采样 + 内部格点 → 全部顶点做三角剖分 → 按三角形重心是否
落在掩码内过滤」。重心过滤有两个固有缺口：

* 一条细长或凹陷的图层，会存在「重心在掩码内、但三个角跨出凹口」的三角形
  和被丢掉的有效区域——掩码边缘的像素可能**落在所有保留三角形之外**；
* 内部格点间距是固定的，比间距还窄的部件（实测 collar 的细缝只有 2~3 px）
  连一个内部格点都拿不到，只剩稀疏轮廓点，三角面直接跨过整条部件。

「把人物全部瞄点住」要求的是**覆盖可证明**，而不是「大致贴合」。所以这里
改成格网式自适应细分：把像素格当成最小的锚定单元，只在掩码覆盖到的格里
布三角形，部分覆盖的格继续四分细分。这样任何被掩码命中的格子都会至少
产出一个三角形，覆盖在构造上就是 100%，可以逐像素验收。

三角形数量只与掩码面积/间距相关，不依赖凸包质量，因此细长部件也不会退化。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

try:  # pragma: no cover - cv2 是桌面必装依赖
    import cv2  # type: ignore
    HAS_CV2 = True
except ImportError:  # pragma: no cover
    HAS_CV2 = False

#: 格网在掩码内的判定：格子里至少这么多像素才算「被命中」。
#: 取 1 是刻意的——任何像素都要被某个格收进去，否则覆盖无法保证。
HIT_MIN_PIXELS = 1

#: 完全覆盖的判定：格内没有透明像素才算「实心格」。
FULL_RATIO = 1.0


class PixelGridMesher:
    """把布尔掩码切成自适应的四边形格网，并输出三角形列表。

    Args:
        spacing: 初始格边长（像素）。越大三角形越少、越平滑，但边界越粗。
        min_cell: 细分下界（像素）。部分覆盖的格会一直细到这么小，
            边界贴合度基本由它决定。
        max_depth: 细分层数上限，防止间距与下界不成比例时无限递归。

    产出的 ``vertices`` 全部落在像素格交点上，所以每个顶点天然是「瞄在
    像素网格上的锚点」，可以直接当 ArtMesh 顶点或骨骼控制点用。
    """

    def __init__(
        self,
        spacing: int = 16,
        min_cell: int = 4,
        max_depth: int = 6,
    ) -> None:
        self.spacing = max(2, int(spacing))
        self.min_cell = max(1, int(min_cell))
        self.max_depth = max(0, int(max_depth))
        if self.min_cell > self.spacing:
            self.min_cell = self.spacing

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def build(self, mask: np.ndarray) -> Dict:
        """为单个掩码生成格网网格。

        Returns:
            ``{"vertices", "vertices_norm", "indices", "width", "height",
            "cells", "stats"}``；空掩码返回 ``_empty_mesh``。
        """
        mask = np.asarray(mask, dtype=bool)
        if mask.ndim != 2:
            raise ValueError(f"mask 必须是二维，收到 shape={mask.shape}")
        h, w = mask.shape
        if not mask.any():
            return self._empty_mesh(w, h)

        ys, xs = np.where(mask)
        x_min, x_max = int(xs.min()), int(xs.max()) + 1
        y_min, y_max = int(ys.min()), int(ys.max()) + 1

        vertices: List[Tuple[int, int]] = []
        index_of: Dict[Tuple[int, int], int] = {}
        indices: List[Tuple[int, int, int]] = []
        cells: List[Tuple[int, int, int, int]] = []

        def vertex(x: int, y: int) -> int:
            key = (x, y)
            got = index_of.get(key)
            if got is None:
                got = len(vertices)
                index_of[key] = got
                vertices.append(key)
            return got

        def emit_cell(x0: int, y0: int, x1: int, y1: int) -> None:
            a = vertex(x0, y0)
            b = vertex(x1, y0)
            c = vertex(x1, y1)
            d = vertex(x0, y1)
            indices.append((a, b, c))
            indices.append((a, c, d))
            cells.append((x0, y0, x1, y1))

        # 根格对齐到 spacing 网格，便于相邻层共享顶点位置（缝合时更稳）
        start_x = (x_min // self.spacing) * self.spacing
        start_y = (y_min // self.spacing) * self.spacing

        root_count = 0
        for y0 in range(start_y, y_max, self.spacing):
            for x0 in range(start_x, x_max, self.spacing):
                root_count += 1
                self._refine(
                    mask, x0, y0,
                    min(x0 + self.spacing, w), min(y0 + self.spacing, h),
                    0, emit_cell,
                )

        pts = np.array(vertices, dtype=float) if vertices else np.zeros((0, 2))
        norm = pts.copy()
        if w > 0 and h > 0:
            norm[:, 0] /= float(w)
            norm[:, 1] /= float(h)

        stats = {
            "root_cells": root_count,
            "kept_cells": len(cells),
            "vertices": len(vertices),
            "triangles": len(indices),
            "spacing": self.spacing,
            "min_cell": self.min_cell,
        }
        return {
            "vertices": pts,
            "vertices_norm": norm,
            "indices": indices,
            "width": w,
            "height": h,
            "cells": cells,
            "stats": stats,
        }

    # ------------------------------------------------------------------
    # 递归细分
    # ------------------------------------------------------------------

    def _refine(
        self,
        mask: np.ndarray,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        depth: int,
        emit_cell,
    ) -> None:
        """把一个格子切成「全保留 / 继续四分 / 丢弃」三种归宿。

        判据只有一条：格里有没有掩码像素。**命中就一定要出三角形**，否则
        覆盖无法保证；正因为如此，「完全被掩码填满」的格才允许不再细分。
        """
        if x1 <= x0 or y1 <= y0:
            return
        sub = mask[y0:y1, x0:x1]
        if sub.size == 0:
            return
        hit = int(sub.sum())
        if hit < HIT_MIN_PIXELS:
            return

        cw, ch = x1 - x0, y1 - y0
        solid = hit == sub.size and FULL_RATIO >= 1.0
        if solid:
            emit_cell(x0, y0, x1, y1)
            return

        # 还能不能继续四分：边长要够大，且没到深度上限
        can_split = (
            depth < self.max_depth
            and cw >= 2 * self.min_cell
            and ch >= 2 * self.min_cell
        )
        if not can_split:
            emit_cell(x0, y0, x1, y1)
            return

        mx = x0 + cw // 2
        my = y0 + ch // 2
        if mx <= x0 or my <= y0 or mx >= x1 or my >= y1:
            emit_cell(x0, y0, x1, y1)
            return

        for cy0, cy1 in ((y0, my), (my, y1)):
            for cx0, cx1 in ((x0, mx), (mx, x1)):
                self._refine(mask, cx0, cy0, cx1, cy1, depth + 1, emit_cell)

    # ------------------------------------------------------------------
    @staticmethod
    def _empty_mesh(w: int = 0, h: int = 0) -> Dict:
        return {
            "vertices": np.zeros((0, 2), dtype=float),
            "vertices_norm": np.zeros((0, 2), dtype=float),
            "indices": [],
            "width": w,
            "height": h,
            "cells": [],
            "stats": {
                "root_cells": 0,
                "kept_cells": 0,
                "vertices": 0,
                "triangles": 0,
            },
        }


# ----------------------------------------------------------------------
# 覆盖度量与验收
# ----------------------------------------------------------------------

def rasterize_mesh(
    mesh: Dict,
    shape: Tuple[int, int],
    *,
    use_cells: bool = False,
) -> np.ndarray:
    """把网格光栅化成布尔覆盖图。

    Args:
        mesh: :meth:`PixelGridMesher.build` 的返回值。
        shape: ``(h, w)``。
        use_cells: 用四边形格光栅化（格网网格的三角形正好铺满格，二者等价，
            但格更少、更快，适合大图层验收）。

    Returns:
        与 ``shape`` 同尺寸的布尔数组：``True`` 表示被网格覆盖。
    """
    h, w = shape
    out = np.zeros((h, w), dtype=np.uint8)
    if not HAS_CV2:
        raise RuntimeError("光栅化需要 OpenCV")

    if use_cells and mesh.get("cells"):
        for x0, y0, x1, y1 in mesh["cells"]:
            cv2.rectangle(out, (int(x0), int(y0)), (int(x1), int(y1)), 1, -1)
        return out.astype(bool)

    pts = np.asarray(mesh.get("vertices"), dtype=float)
    idx = mesh.get("indices") or []
    if pts.size == 0 or not idx:
        return out.astype(bool)
    for tri in idx:
        poly = np.round(pts[list(tri)]).astype(np.int32)
        cv2.fillPoly(out, [poly], 1)
    return out.astype(bool)


def measure_coverage(mask: np.ndarray, mesh: Dict) -> Dict:
    """逐像素验收：图层像素被网格覆盖了多少、网格又超出图层多少。

    Returns:
        ``{"mask_px", "covered_px", "uncovered_px", "coverage",
        "mesh_px", "over_cover", "vertices", "triangles"}``

    ``coverage`` 是 **掩码被网格覆盖的比例**，这是「全部瞄点住」的判据；
    ``over_cover`` 是网格里落在掩码之外的比例，用来衡量贴合的松紧——
    偏大说明该部件间距太粗，网格把大片透明区域也框了进来。
    """
    mask = np.asarray(mask, dtype=bool)
    covered = rasterize_mesh(mesh, mask.shape)
    mask_px = int(mask.sum())
    covered_px = int((mask & covered).sum())
    mesh_px = int(covered.sum())
    # 注意：vertices 是 numpy 数组，不能用 `or []` 判空（numpy 会抛
    # "truth value of an array is ambiguous"），Generator 里已经踩过同一个坑。
    verts = mesh.get("vertices")
    if verts is None:
        verts = []
    tris = mesh.get("indices")
    if tris is None:
        tris = []
    return {
        "mask_px": mask_px,
        "covered_px": covered_px,
        "uncovered_px": mask_px - covered_px,
        "coverage": (covered_px / mask_px) if mask_px else 1.0,
        "mesh_px": mesh_px,
        "over_cover": ((mesh_px - covered_px) / mesh_px) if mesh_px else 0.0,
        "vertices": int(len(verts)),
        "triangles": int(len(tris)),
    }


def coverage_of_parts(
    masks: Dict[str, np.ndarray],
    meshes: Dict[str, Dict],
    silhouette: Optional[np.ndarray] = None,
) -> Dict:
    """整体验收：所有部件的网格合起来覆盖了多少角色像素。

    Args:
        masks: 部件名 -> 掩码。
        meshes: 部件名 -> 网格。
        silhouette: 角色轮廓。给了才能算「角色像素是否 100% 被瞄住」；
            为空时退化为「所有部件掩码的并集」。

    Returns:
        总体统计 + 逐部件明细 + 未覆盖像素数。
    """
    if not masks:
        return {
            "target_px": 0, "covered_px": 0, "coverage": 1.0,
            "uncovered_px": 0, "parts": {},
        }

    first = next(iter(masks.values()))
    shape = first.shape
    target = np.zeros(shape, dtype=bool)
    for m in masks.values():
        target |= np.asarray(m, dtype=bool)
    if silhouette is not None:
        sil = np.asarray(silhouette, dtype=bool)
        target = target | sil

    union = np.zeros(shape, dtype=bool)
    details: Dict[str, Dict] = {}
    for name, mask in masks.items():
        mesh = meshes.get(name) or PixelGridMesher._empty_mesh(*shape[::-1])
        covered = rasterize_mesh(mesh, shape)
        union |= covered
        details[name] = measure_coverage(mask, mesh)

    target_px = int(target.sum())
    covered_px = int((target & union).sum())
    return {
        "target_px": target_px,
        "covered_px": covered_px,
        "uncovered_px": target_px - covered_px,
        "coverage": (covered_px / target_px) if target_px else 1.0,
        "mesh_px": int(union.sum()),
        "parts": details,
    }
