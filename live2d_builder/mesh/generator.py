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

        return self._build_delaunay(vertices, mask, w, h)

    # ------------------------------------------------------------------
    # Skinning weights
    # ------------------------------------------------------------------

    def compute_bone_weights(
        self,
        mesh: Dict,
        bone_positions: Dict[str, Tuple[float, float]],
        assigned_bone: str,
        parent_bone: Optional[str] = None,
    ) -> Dict:
        """Compute per-vertex skinning weights via inverse-distance blending.

        Each vertex is weighted between the ``assigned_bone`` and the optional
        ``parent_bone``: the closer a vertex is to a bone's position, the more
        strongly that bone influences it. Weights for every vertex are
        normalised to sum to 1.0.

        Returns:
            dict with ``bone_names`` (the influencing bones in weight order)
            and ``weights`` (a list of per-vertex weight lists, aligned with
            ``mesh["vertices"]``).
        """
        # Build the ordered list of influencing bones (assigned first).
        bone_names: List[str] = []
        if assigned_bone and assigned_bone in bone_positions:
            bone_names.append(assigned_bone)
        if parent_bone and parent_bone in bone_positions and parent_bone not in bone_names:
            bone_names.append(parent_bone)
        if not bone_names:
            # Nothing to bind to; pin to the assigned bone / Root with full weight.
            bone_names = [assigned_bone or "Root"]

        verts = mesh.get("vertices")
        if verts is None or len(verts) == 0:
            return {"bone_names": bone_names, "weights": []}

        weights: List[List[float]] = []
        for v in verts:
            vx, vy = float(v[0]), float(v[1])
            invs: List[float] = []
            for bn in bone_names:
                bx, by = bone_positions.get(bn, (0.0, 0.0))
                dist = math.hypot(vx - float(bx), vy - float(by))
                # Inverse-square distance gives a smooth falloff; guard the
                # degenerate (vertex exactly on the bone) case.
                invs.append(1.0 / max(dist * dist, 1e-6))
            total = sum(invs)
            if total <= 0:
                w = [1.0] + [0.0] * (len(bone_names) - 1)
            else:
                w = [inv / total for inv in invs]
            # Guarantee exact summation to 1.0 within float tolerance.
            drift = 1.0 - sum(w)
            w[0] += drift
            weights.append([round(x, 6) for x in w])

        return {"bone_names": bone_names, "weights": weights}

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
