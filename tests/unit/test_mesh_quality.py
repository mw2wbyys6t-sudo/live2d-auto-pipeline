"""Mesh triangle-quality contract borrowed from Cubism's auto-mesh.

Cubism relaxes its interior grid (half-weight Laplacian, boundary pinned) so
triangles stay close to equilateral. Skinny triangles sample the texture badly
under deformation: a sliver stretches into a long streak when its vertices move.
This module pins two things for the production ``generate_combined_mesh`` path:

1. **Quality** - interior triangles are not slivers (minimum interior angle floor,
   bounded aspect ratio).
2. **Coverage** - the GPU pixel-center coverage of the layer mask does not regress
   (boundary vertices stay on the contour, so the seam/outline guards still hold).

The metrics here are the instrument; the floors are set above the pre-improvement
baseline measured on the same shapes.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image

from live2d_builder.mesh.generator import MeshGenerator


# ----------------------------------------------------------------------
# Instruments
# ----------------------------------------------------------------------

def triangle_quality(vertices, indices):
    """Per-triangle interior angles (deg) and aspect ratios.

    aspect = longest_edge^2 / (4*sqrt(3)*area); 1.0 == equilateral.
    Returns min_angle, median_min_angle, mean_aspect, max_aspect,
    skinny_fraction (min angle < 15 deg), degenerate count.
    """
    verts = np.asarray(vertices, dtype=float)
    min_angles = []
    aspects = []
    degenerate = 0
    for tri in indices:
        a, b, c = verts[list(tri)]
        ab = b - a
        ac = c - a
        bc = c - b
        cross = ab[0] * ac[1] - ab[1] * ac[0]
        area2 = abs(cross)
        if area2 <= 1e-8:
            degenerate += 1
            continue
        # interior angles via atan2(|cross|, dot) at each corner
        angles = []
        for (p, q, r) in ((a, b, c), (b, c, a), (c, a, b)):
            u = q - p
            v = r - p
            dot = u[0] * v[0] + u[1] * v[1]
            crs = abs(u[0] * v[1] - u[1] * v[0])
            angles.append(math.degrees(math.atan2(crs, dot)))
        min_angles.append(min(angles))
        e = [float(np.linalg.norm(ab)), float(np.linalg.norm(bc)),
             float(np.linalg.norm(ac))]
        longest = max(e)
        area = 0.5 * area2
        aspects.append((longest * longest) / (4.0 * math.sqrt(3.0) * area))
    if not min_angles:
        return {
            "min_angle": 0.0, "median_min_angle": 0.0, "mean_aspect": 0.0,
            "max_aspect": 0.0, "skinny_fraction": 1.0, "degenerate": degenerate,
            "triangles": 0,
        }
    ma = np.asarray(min_angles)
    ar = np.asarray(aspects)
    return {
        "min_angle": float(ma.min()),
        "median_min_angle": float(np.median(ma)),
        "mean_aspect": float(ar.mean()),
        "max_aspect": float(ar.max()),
        "skinny_fraction": float((ma < 15.0).mean()),
        "degenerate": degenerate,
        "triangles": len(min_angles),
    }


def gpu_coverage(mesh, mask):
    """Pixel-center rasterization (GPU convention), same口径 as test_edge_bleed."""
    mask = np.asarray(mask, dtype=bool)
    h, w = mask.shape
    out = np.zeros((h, w), dtype=bool)
    verts = np.asarray(mesh.get("vertices"), dtype=float)
    if verts.size == 0 or not mesh.get("indices"):
        return {"coverage": 0.0, "over_cover": 0.0}
    for tri in mesh["indices"]:
        p = verts[list(tri)]
        x0 = max(int(np.floor(p[:, 0].min())), 0)
        x1 = min(int(np.ceil(p[:, 0].max())) + 1, w)
        y0 = max(int(np.floor(p[:, 1].min())), 0)
        y1 = min(int(np.ceil(p[:, 1].max())) + 1, h)
        if x1 <= x0 or y1 <= y0:
            continue
        x, y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        e = [(p[(i + 1) % 3][0] - p[i][0]) * (y - p[i][1])
             - (p[(i + 1) % 3][1] - p[i][1]) * (x - p[i][0]) for i in range(3)]
        out[y0:y1, x0:x1] |= ((e[0] >= 0) & (e[1] >= 0) & (e[2] >= 0)) | \
                             ((e[0] <= 0) & (e[1] <= 0) & (e[2] <= 0))
    mask_px = int(mask.sum())
    covered_px = int((mask & out).sum())
    mesh_px = int(out.sum())
    return {
        "coverage": covered_px / mask_px if mask_px else 1.0,
        "over_cover": (mesh_px - covered_px) / mesh_px if mesh_px else 0.0,
    }


# ----------------------------------------------------------------------
# Representative shapes (all > TINY_FEATURE_PIXELS so they take Delaunay path)
# ----------------------------------------------------------------------

def _rgba(mask):
    arr = np.zeros(mask.shape + (4,), dtype=np.uint8)
    arr[mask] = (200, 120, 90, 255)
    return Image.fromarray(arr, "RGBA")


def _disc(h=200, w=200, r=60):
    yy, xx = np.mgrid[0:h, 0:w]
    return (xx - w / 2) ** 2 + (yy - h / 2) ** 2 <= r ** 2


def _rect(h=200, w=200):
    m = np.zeros((h, w), dtype=bool)
    m[20:180, 30:170] = True
    return m


def _ellipse(h=200, w=200):
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx - 100) / 70.0) ** 2 + ((yy - 100) / 45.0) ** 2 <= 1.0


def _concave_u(h=200, w=200):
    m = np.zeros((h, w), dtype=bool)
    m[20:180, 30:170] = True
    m[20:120, 70:130] = False  # notch open at the top
    return m


def _thin_limb(h=200, w=200):
    m = np.zeros((h, w), dtype=bool)
    m[20:180, 90:112] = True  # 22px wide, 160px tall
    return m


SHAPES = {
    "disc": _disc,
    "rect": _rect,
    "ellipse": _ellipse,
    "concave_u": _concave_u,
    "thin_limb": _thin_limb,
}


def _measure(name):
    mask = SHAPES[name]()
    mesh = MeshGenerator().generate_combined_mesh(_rgba(mask))
    q = triangle_quality(mesh["vertices"], mesh["indices"])
    cov = gpu_coverage(mesh, mask)
    return mask, mesh, q, cov


def test_report_baseline(capsys):
    """Print the instrument reading for every shape (measurement, not a gate)."""
    for name in SHAPES:
        mask, mesh, q, cov = _measure(name)
        print(
            f"{name:10s} verts={len(mesh['vertices']):4d} tris={q['triangles']:4d} "
            f"min_ang={q['min_angle']:5.1f} med_ang={q['median_min_angle']:5.1f} "
            f"mean_ar={q['mean_aspect']:5.2f} max_ar={q['max_aspect']:6.2f} "
            f"skinny={q['skinny_fraction']:.3f} degen={q['degenerate']} "
            f"cov={cov['coverage']:.4f} over={cov['over_cover']:.3f}"
        )


# ----------------------------------------------------------------------
# Quality gate (RED before the hex-grid + Laplacian improvement)
# ----------------------------------------------------------------------

def test_broad_shape_triangles_are_near_equilateral():
    """A relaxed hexagonal interior grid keeps broad-shape triangles regular.

    Pre-improvement baseline: disc median min-angle 38.7 deg (square grid meets
    the contour in slivers). Cubism's half-weight Laplacian relaxation pulls the
    interior toward equilateral; the median interior angle must reach 45 deg.
    """
    _, _, q, _ = _measure("disc")
    assert q["median_min_angle"] >= 45.0, q


def test_relaxation_does_not_invert_triangles():
    """Relaxation must never produce a degenerate/inverted triangle."""
    for name in SHAPES:
        _, _, q, _ = _measure(name)
        assert q["degenerate"] == 0, (name, q)


# ----------------------------------------------------------------------
# Coverage non-regression guards (floors pinned at the measured baseline)
# ----------------------------------------------------------------------

#: GPU pixel-center coverage measured on the pre-improvement generator. Boundary
#: vertices stay on the contour, so quality changes must not drop below these.
_COVERAGE_FLOOR = {
    "disc": 0.985,
    "rect": 0.985,
    "ellipse": 0.980,
    "concave_u": 0.972,
    "thin_limb": 0.945,
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_coverage_does_not_regress(name):
    _, _, _, cov = _measure(name)
    assert cov["coverage"] >= _COVERAGE_FLOOR[name], (name, cov)


def test_relax_interior_pins_boundary_and_smooths_interior():
    """Direct contract of ``_relax_interior``: contour stays put, interior moves.

    A fan triangulation of a square with one off-centre interior vertex. The four
    corners lie on boundary edges (used by one triangle) so they must not move;
    the interior vertex must relax toward its neighbours' centroid (the centre).
    Disabling relaxation (RELAX_WEIGHT=0) makes this fail.
    """
    gen = MeshGenerator()
    verts = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0],
                      [0.0, 100.0], [30.0, 30.0]])
    indices = [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    mesh = {"vertices": verts.copy(), "vertices_norm": verts.copy() / 100.0,
            "indices": indices, "width": 100, "height": 100}
    mask = np.ones((100, 100), dtype=np.uint8)

    out = gen._relax_interior(mesh, mask)
    ov = np.asarray(out["vertices"], dtype=float)

    # boundary corners pinned exactly
    assert np.allclose(ov[:4], verts[:4]), ov[:4]
    # interior vertex relaxed toward the centre (50, 50)
    assert ov[4, 0] > 45.0 and ov[4, 1] > 45.0, ov[4]
    # no triangle inverted or collapsed
    q = triangle_quality(ov, out["indices"])
    assert q["degenerate"] == 0, q