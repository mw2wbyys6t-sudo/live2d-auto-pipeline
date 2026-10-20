#!/usr/bin/env python3
"""Generate deformable triangular meshes from RGBA layer images.

Supports Delaunay triangulation with:
- Boundary subdivision for smooth edges
- Grid-based interior vertices (adjustable spacing)
- Contour-following meshes for organic shapes
- Mesh quality validation (degenerate triangle rejection, aspect ratio)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import Delaunay
from scipy.spatial import QhullError

from core.logger import get_logger

log = get_logger("rigging.mesh")


class MeshGenerator:
    """Create triangulated meshes from opaque regions of RGBA images.

    All returned vertex coordinates are in pixel space. Normalized (0-1)
    coordinates are included under the ``vertices_norm`` key when the
    image dimensions are non-zero.
    """

    def __init__(
        self,
        internal_spacing: int = 24,
        contour_spacing: int = 12,
        alpha_threshold: int = 128,
    ) -> None:
        self.internal_spacing = max(4, internal_spacing)
        self.contour_spacing = max(4, contour_spacing)
        self.alpha_threshold = max(1, min(255, alpha_threshold))

    #: 小部件兜底阈值（像素）。轮廓采样与 Delaunay 都要求足够多的边界点，低于这个
    #: 规模的图层会得到 0 顶点、整层从模型里**静默消失**：实测 demo 立绘的嘴 27px、
    #: 鼻 11px 全部丢失，于是 ParamMouthOpenY 连可驱动的网格都不存在。
    #: Live2D 里这类五官本来就是一张四顶点面片，兜底直接给包围盒四边形。
    TINY_FEATURE_PIXELS = 400

    def _tiny_feature_mesh(self, mask: np.ndarray, w: int, h: int) -> Dict:
        """小部件兜底：用 alpha 包围盒的 **3×2 面片**代替 Delaunay 网格。

        四边形覆盖的区域里，图层本身是透明的，所以画面不会多出像素；只是让
        "有内容但点太少" 的图层也能进模型、能被参数驱动。

        为什么是 3 列而不是 4 个角点：变形器/键形只能移动**顶点**，4 角面片的内部
        形变只能是双线性插值，中间的列即使被控制网格推动也不影响任何顶点 ——
        实测 ``ParamMouthForm``（口型）与 ``ParamBrowAngle``（眉角）这类"两端动、
        中间不动"的形变，在 4 角面片上退化成整体平移（嘴整体上移 4px）。
        加一列中间顶点后，弯曲才真的能表达出来。
        """
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return self._empty_mesh(w, h)
        # 四周各留 1px：抗锯齿边低于 alpha_threshold 不计入 mask，没余量会被切掉
        x0 = float(max(0, int(xs.min()) - 1))
        x1 = float(min(w - 1, int(xs.max()) + 1))
        y0 = float(max(0, int(ys.min()) - 1))
        y1 = float(min(h - 1, int(ys.max()) + 1))
        if x1 - x0 < 2.0 or y1 - y0 < 1.0:
            return self._empty_mesh(w, h)
        xm = (x0 + x1) / 2.0
        pts = np.array([[x0, y0], [xm, y0], [x1, y0],
                        [x0, y1], [xm, y1], [x1, y1]], dtype=float)
        norm = pts.copy()
        norm[:, 0] /= w
        norm[:, 1] /= h
        return {
            "vertices": pts,
            "vertices_norm": norm,
            # 2 行 × 2 列 = 4 个四边形、8 个三角形
            "indices": [[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]],
            "width": w,
            "height": h,
        }

    # ------------------------------------------------------------------
    # Primary entry points
    # ------------------------------------------------------------------

    def generate(self, image: Image.Image) -> Dict:
        """Generate a Delaunay mesh for a single layer (backward compatible).

        Returns:
            dict with keys ``vertices`` (N,2 float array in pixel space),
            ``vertices_norm`` (N,2 float array normalised to image size),
            ``indices`` (list of 3-tuples), ``width``, ``height``.
            Empty mesh returned when the alpha channel is fully transparent.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image)
        alpha = arr[:, :, 3]
        h, w = alpha.shape

        mask = (alpha > self.alpha_threshold).astype(np.uint8)
        if mask.sum() == 0:
            return self._empty_mesh(w, h)

        vertices, vertices_norm, indices = self._triangulate(mask, w, h)
        if vertices is None:
            return self._empty_mesh(w, h)
        return {
            "vertices": vertices,
            "vertices_norm": vertices_norm,
            "indices": indices,
            "width": w,
            "height": h,
        }

    def generate_grid_mesh(self, image: Image.Image, spacing: int = 20) -> Dict:
        """Generate a regular grid mesh (uniform spacing).

        Args:
            image: RGBA layer image.
            spacing: Distance between interior grid vertices in pixels.

        Returns:
            Mesh dict with pixel + normalised vertices and triangle indices.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image)
        alpha = arr[:, :, 3]
        h, w = alpha.shape

        mask = (alpha > self.alpha_threshold).astype(np.uint8)
        if mask.sum() == 0:
            return self._empty_mesh(w, h)

        # Find bounding box
        ys, xs = np.where(mask > 0)
        x_min, x_max = int(xs.min()), int(xs.max())
        y_min, y_max = int(ys.min()), int(ys.max())

        vertices: List[Tuple[float, float]] = []

        # Boundary vertices via contour
        boundary_pts = self._sample_contour(mask)
        vertices.extend(boundary_pts)

        # Interior grid
        spacing = max(4, spacing)
        for y in range(y_min + spacing, y_max, spacing):
            for x in range(x_min + spacing, x_max, spacing):
                if 0 <= y < h and 0 <= x < w and mask[y, x] > 0:
                    vertices.append((float(x), float(y)))

        return self._build_delaunay(vertices, mask, w, h)

    def generate_contour_mesh(self, image: Image.Image, spacing: int = 18) -> Dict:
        """Generate a contour-following mesh good for organic shapes.

        Vertices are placed along the alpha contour at approximately
        ``spacing`` pixel intervals, with a sparse interior grid.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image)
        alpha = arr[:, :, 3]
        h, w = alpha.shape

        mask = (alpha > self.alpha_threshold).astype(np.uint8)
        if mask.sum() == 0:
            return self._empty_mesh(w, h)

        vertices: List[Tuple[float, float]] = []

        # Dense boundary sampling
        boundary_pts = self._sample_contour(mask, target_spacing=spacing)
        vertices.extend(boundary_pts)

        # Add some interior points spaced further apart for rigidity
        ys, xs = np.where(mask > 0)
        x_min, x_max = int(xs.min()), int(xs.max())
        y_min, y_max = int(ys.min()), int(ys.max())
        interior_spacing = max(spacing * 2, 16)
        for y in range(y_min + interior_spacing, y_max, interior_spacing):
            for x in range(x_min + interior_spacing, x_max, interior_spacing):
                if 0 <= y < h and 0 <= x < w and mask[y, x] > 0:
                    vertices.append((float(x), float(y)))

        return self._build_delaunay(vertices, mask, w, h)

    def generate_combined_mesh(
        self,
        image: Image.Image,
        grid_spacing: int = 20,
        contour_spacing: int = 12,
    ) -> Dict:
        """Generate a mesh combining dense contour boundary and interior grid.

        This is the recommended method for most character layers as it
        gives smooth edges while maintaining interior stability.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image)
        alpha = arr[:, :, 3]
        h, w = alpha.shape

        mask = (alpha > self.alpha_threshold).astype(np.uint8)
        if mask.sum() == 0:
            return self._empty_mesh(w, h)

        vertices: List[Tuple[float, float]] = []

        # Dense contour
        boundary_pts = self._sample_contour(mask, target_spacing=contour_spacing)
        vertices.extend(boundary_pts)

        # Subdivided boundary (insert extra points between consecutive contour pts)
        vertices = self._subdivide_boundary(vertices, target_spacing=contour_spacing)

        # Interior grid
        ys, xs = np.where(mask > 0)
        if len(xs) > 0:
            x_min, x_max = int(xs.min()), int(xs.max())
            y_min, y_max = int(ys.min()), int(ys.max())
            for y in range(y_min + grid_spacing, y_max, grid_spacing):
                for x in range(x_min + grid_spacing, x_max, grid_spacing):
                    if 0 <= y < h and 0 <= x < w and mask[y, x] > 0:
                        vertices.append((float(x), float(y)))

        mesh = self._build_delaunay(vertices, mask, w, h)
        if int(mask.sum()) <= self.TINY_FEATURE_PIXELS:
            # 五官这类小图层一律用四顶点面片，**不管 Delaunay 有没有出结果**。
            # 轮廓采样按 12px 间距取点，39×30 的眼睛只取到 6 个边界点，再经
            # 三角化+质心过滤后只有 9 个三角形：实测网格只盖住眼睛的 27%
            # （眼白 IoU 0.275、瞳孔 0.392），渲染出来就是几块碎片。
            # 面片覆盖整个 alpha 包围盒，包围盒内的图层像素本身是透明的，
            # 所以画面不会多出东西，只是不会再把五官切碎。
            quad = self._tiny_feature_mesh(mask, w, h)
            if len(quad["vertices"]):
                if len(mesh["vertices"]):
                    log.debug(
                        f"tiny feature ({int(mask.sum())}px) quad fallback "
                        f"(was {len(mesh['vertices'])} verts / "
                        f"{len(mesh['indices'])} tris)")
                mesh = quad
        return mesh

    # ------------------------------------------------------------------
    # Skinning weights
    # ------------------------------------------------------------------

    @staticmethod
    def compute_bone_weights(
        mesh: Dict,
        bone_positions: Dict[str, Tuple[float, float]],
        assigned_bone: str,
        parent_bone: Optional[str] = None,
    ) -> Dict:
        """为单个网格计算逐顶点蒙皮权重（ assigned 骨骼 + 父骨骼）。

        返回 ``{"bone_names": [...], "weights": [[...], ...]}``，其中
        ``weights[i][j]`` 是顶点 *i* 在 ``bone_names[j]`` 上的权重，和为 1。

        权重模型（从简到够用，不引入臆造的骨骼动力学）：

        * 顶点只受 **assigned 与其父骨骼** 影响 —— 自动绑骨的语义就是"整层
          跟着它归属的那根骨骼走"，再多分骨骼既无输入数据也无从验证。
        * 两者按**到各自枢轴的距离倒数**分配，使靠近枢轴的部分更"钉"在骨骼上；
          距离为 0 时该骨骼独占权重。
        * 父骨骼缺席（根骨骼）时 assigned 独占全部权重。

        这个格式正是 ``moc3_pipeline.rotation_keyforms`` /
        ``head_angle_keyforms`` 消费的输入：没有它，参数键形根本不会被烘焙，
        moc3 里 ``KEYS``/``KEYFORM_BINDINGS`` 恒为 0，模型"能画不动"。
        """
        # 注意：mesh["vertices"] 可能是 numpy 数组，不能用 `or []` 判空
        # （那会抛 "truth value is ambiguous"）。
        vertices = mesh.get("vertices")
        if vertices is None:
            vertices = []
        if len(vertices) == 0:
            return {"bone_names": [], "weights": []}

        names = [assigned_bone]
        if parent_bone and parent_bone != assigned_bone:
            names.append(parent_bone)

        pivots = []
        for name in names:
            pos = bone_positions.get(name)
            pivots.append((float(pos[0]), float(pos[1])) if pos else None)

        if all(p is None for p in pivots):
            # 一根骨骼位置都没有：退化为单骨骼全权重，至少让键形能被烘焙出来。
            return {"bone_names": [assigned_bone],
                    "weights": [[1.0]] * len(vertices)}

        weights: List[List[float]] = []
        for x, y in vertices:
            x, y = float(x), float(y)
            # 距离为 0 => 该骨骼独占（其余骨骼分文不取）；否则按距离倒数分配。
            at_pivot = []
            dists = []
            for pivot in pivots:
                if pivot is None:
                    at_pivot.append(False)
                    dists.append(float("inf"))
                    continue
                d = math.hypot(x - pivot[0], y - pivot[1])
                if d <= 1e-9:
                    at_pivot.append(True)
                    dists.append(0.0)
                else:
                    at_pivot.append(False)
                    dists.append(d)

            row = [0.0] * len(names)
            if any(at_pivot):
                # 多个枢轴重合（父子骨骼同点）时平均分配，仍严格和为 1。
                hit = [i for i, flag in enumerate(at_pivot) if flag]
                share = 1.0 / len(hit)
                for i in hit:
                    row[i] = share
            else:
                inv = [0.0 if math.isinf(d) else 1.0 / d for d in dists]
                total = sum(inv)
                if total > 0.0 and math.isfinite(total):
                    row = [v / total for v in inv]
            weights.append(row)

        return {"bone_names": names, "weights": weights}

    # ------------------------------------------------------------------
    # Mesh quality validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_mesh_quality(
        vertices: np.ndarray,
        indices: List[Tuple[int, int, int]],
        min_edge_length: float = 1.0,
        max_aspect_ratio: float = 20.0,
    ) -> Dict:
        """Check mesh quality metrics.

        Returns:
            dict with ``valid`` (bool), ``degenerate_count``, ``aspect_ratios``
            (list of float), ``max_aspect_ratio``, ``min_edge``, ``warnings``.
        """
        warnings: List[str] = []
        if len(indices) == 0 or len(vertices) < 3:
            return {
                "valid": False,
                "degenerate_count": 0,
                "aspect_ratios": [],
                "max_aspect_ratio": 0.0,
                "min_edge": 0.0,
                "warnings": ["Empty mesh"],
            }

        aspect_ratios: List[float] = []
        min_edge = float("inf")
        degenerate = 0

        for tri in indices:
            p0, p1, p2 = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
            e0 = float(np.linalg.norm(p1 - p0))
            e1 = float(np.linalg.norm(p2 - p1))
            e2 = float(np.linalg.norm(p0 - p2))
            edges = [e0, e1, e2]
            min_edge = min(min_edge, min(edges))

            # Area via cross product
            area = 0.5 * abs(
                (p1[0] - p0[0]) * (p2[1] - p0[1])
                - (p2[0] - p0[0]) * (p1[1] - p0[1])
            )
            if area < 1e-6:
                degenerate += 1
                aspect_ratios.append(float("inf"))
                continue

            # Aspect ratio: longest edge / (2 * sqrt(3) * inradius)
            longest = max(edges)
            # aspect ~ longest^2 / (4*sqrt(3)*area)
            ar = (longest * longest) / (4.0 * math.sqrt(3.0) * area) if area > 0 else float("inf")
            aspect_ratios.append(ar)

        finite_ratios = [r for r in aspect_ratios if math.isfinite(r)]
        max_ar = max(finite_ratios) if finite_ratios else float("inf")

        if min_edge < min_edge_length:
            warnings.append(f"Minimum edge length {min_edge:.2f}px below threshold {min_edge_length}")
        if max_ar > max_aspect_ratio:
            warnings.append(f"Max aspect ratio {max_ar:.1f} exceeds threshold {max_aspect_ratio}")
        if degenerate > 0:
            warnings.append(f"{degenerate} degenerate triangles found")

        return {
            "valid": len(warnings) == 0,
            "degenerate_count": degenerate,
            "aspect_ratios": aspect_ratios,
            "max_aspect_ratio": max_ar if math.isfinite(max_ar) else -1.0,
            "min_edge": min_edge,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _triangulate(
        self, mask: np.ndarray, w: int, h: int
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], List[Tuple[int, int, int]]]:
        """Run the full Delaunay triangulation pipeline used by ``generate``."""
        contour_pts = self._sample_contour(mask)

        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None, None, []
        x_min, x_max = int(xs.min()), int(xs.max())
        y_min, y_max = int(ys.min()), int(ys.max())

        grid_pts: List[Tuple[float, float]] = []
        for y in range(y_min, y_max + 1, self.internal_spacing):
            for x in range(x_min, x_max + 1, self.internal_spacing):
                if 0 <= y < h and 0 <= x < w and mask[y, x] > 0:
                    grid_pts.append((float(x), float(y)))

        vertices = contour_pts + grid_pts
        if len(vertices) < 3:
            return None, None, []

        result = self._build_delaunay(vertices, mask, w, h)
        if len(result["indices"]) == 0:
            return None, None, []
        return result["vertices"], result["vertices_norm"], result["indices"]

    def _sample_contour(
        self, mask: np.ndarray, target_spacing: Optional[int] = None
    ) -> List[Tuple[float, float]]:
        """Sample points along the alpha contour using OpenCV findContours."""
        spacing = target_spacing or self.contour_spacing
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        pts: List[Tuple[float, float]] = []
        for cnt in contours:
            cnt = cnt.squeeze()
            if cnt.ndim < 2 or len(cnt) < 3:
                continue
            arclen = cv2.arcLength(cnt, True)
            if arclen <= 0:
                continue
            # approxPolyDP epsilon as fraction of perimeter
            eps = spacing / arclen
            approx = cv2.approxPolyDP(cnt, eps * arclen, True).squeeze()
            if approx.ndim == 1:
                approx = approx[np.newaxis, :]
            for pt in approx:
                pts.append((float(pt[0]), float(pt[1])))
        return pts

    @staticmethod
    def _subdivide_boundary(
        points: List[Tuple[float, float]],
        target_spacing: float,
    ) -> List[Tuple[float, float]]:
        """Insert extra vertices along the boundary polyline so that no
        consecutive pair is farther apart than ``target_spacing``."""
        if len(points) < 2:
            return list(points)
        result: List[Tuple[float, float]] = []
        n = len(points)
        for i in range(n):
            p0 = points[i]
            p1 = points[(i + 1) % n]
            result.append(p0)
            dx = p1[0] - p0[0]
            dy = p1[1] - p0[1]
            dist = math.hypot(dx, dy)
            if dist <= target_spacing:
                continue
            n_sub = max(1, int(math.ceil(dist / target_spacing)) - 1)
            for k in range(1, n_sub + 1):
                t = k / (n_sub + 1)
                result.append((p0[0] + dx * t, p0[1] + dy * t))
        return result

    def _build_delaunay(
        self,
        vertices: List[Tuple[float, float]],
        mask: np.ndarray,
        w: int,
        h: int,
    ) -> Dict:
        """Run Delaunay triangulation, filter out-of-mask tris, return mesh dict."""
        if len(vertices) < 3:
            return self._empty_mesh(w, h)

        pts = np.array(vertices, dtype=float)
        # Deduplicate
        rounded = np.round(pts, decimals=3)
        _, unique_idx = np.unique(rounded, axis=0, return_index=True)
        pts = pts[np.sort(unique_idx)]
        if len(pts) < 3:
            return self._empty_mesh(w, h)

        # Reject 1-D inputs
        if pts[:, 0].max() - pts[:, 0].min() < 1e-6:
            return self._empty_mesh(w, h)
        if pts[:, 1].max() - pts[:, 1].min() < 1e-6:
            return self._empty_mesh(w, h)

        try:
            tri = Delaunay(pts)
        except (QhullError, ValueError, RuntimeError) as exc:
            log.debug(f"Delaunay failed ({exc}); returning empty mesh")
            return self._empty_mesh(w, h)

        # Keep only triangles whose centroid is inside mask
        kept: List[Tuple[int, int, int]] = []
        for t in tri.simplices:
            cx = int(round(pts[t].mean(axis=0)[0]))
            cy = int(round(pts[t].mean(axis=0)[1]))
            cx = max(0, min(w - 1, cx))
            cy = max(0, min(h - 1, cy))
            if mask[cy, cx] > 0:
                kept.append((int(t[0]), int(t[1]), int(t[2])))

        # Normalise to [0,1]
        if w > 0 and h > 0:
            norm = pts.copy()
            norm[:, 0] /= w
            norm[:, 1] /= h
        else:
            norm = np.zeros_like(pts)

        return {
            "vertices": pts,
            "vertices_norm": norm,
            "indices": kept,
            "width": w,
            "height": h,
        }

    @staticmethod
    def _empty_mesh(w: int = 0, h: int = 0) -> Dict:
        """Return a canonical empty mesh dictionary."""
        return {
            "vertices": np.zeros((0, 2), dtype=float),
            "vertices_norm": np.zeros((0, 2), dtype=float),
            "indices": [],
            "width": w,
            "height": h,
        }
