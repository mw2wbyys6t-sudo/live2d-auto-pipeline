#!/usr/bin/env python3
"""
Live2D Master Agent - K-Means Layer Separation (v6, DEFAULT per P0-3 fix)

Uses K-means clustering on image colors to separate a character into layers.
This is the default layerer when no learned segmentation model is available.
This module also provides :meth:`KMeansLayerer.layer_to_standard_parts` which
maps K-means clusters to the standard semantic part names used elsewhere in
the pipeline (hair_back, face, eyes, etc.) using color/position heuristics.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger

log = get_logger("layering")

# Optional dependencies with graceful degradation
try:
    from sklearn.cluster import KMeans as SKLearnKMeans  # type: ignore
    HAS_SKLEARN = True
except ImportError:  # pragma: no cover
    HAS_SKLEARN = False
    log.debug("scikit-learn not available, using fallback quantization")

try:
    import scipy.ndimage as ndi  # type: ignore  # noqa: F401
    HAS_SCIPY = True
except ImportError:  # pragma: no cover
    HAS_SCIPY = False


class KMeansLayerer:
    """K-means color clustering for Live2D layer separation (v6).

    P0-3: This is the default layerer, replacing the buggy call to layer_pro.

    Args:
        k_clusters: Target number of color clusters (clamped to 3..20).
        alpha_threshold: Alpha value above which a pixel is considered opaque.
        min_layer_area_ratio: Minimum fraction of image area for a cluster
            to be exported as its own layer.
        output_dir: Default output directory for :meth:`layer`.
    """

    def __init__(
        self,
        k_clusters: int = 12,
        alpha_threshold: int = 10,
        min_layer_area_ratio: float = 0.001,
        output_dir: Optional[str] = None,
    ) -> None:
        self.k_clusters: int = max(3, min(k_clusters, 20))
        self.alpha_threshold: int = alpha_threshold
        self.min_layer_area_ratio: float = min_layer_area_ratio
        self.output_dir: Optional[Path] = Path(output_dir) if output_dir else None

    # ------------------------------------------------------------------ public

    def layer(
        self,
        image: Image.Image,
        output_dir: Optional[str] = None,
        label_layers: bool = True,
    ) -> Dict:
        """Perform K-means layering on an image.

        Args:
            image: Source image (any mode; converted to RGBA).
            output_dir: Directory for layer PNGs and guide. Falls back to
                ``self.output_dir`` or a timestamped default.
            label_layers: Unused; accepted for API parity with other layerers.

        Returns:
            Dict with keys: ``layers``, ``output_dir``, ``preview_path``,
            ``guide_path``, ``layer_count``, ``k_clusters``.
        """
        out_dir = Path(output_dir) if output_dir else self.output_dir
        if out_dir is None:
            out_dir = Path.cwd() / "output" / f"layers_{int(time.time())}"
        out_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"K-means layering: k={self.k_clusters}, input={image.size}")

        # Convert to RGBA
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        img_array = np.array(image)
        h, w = img_array.shape[:2]

        # Separate alpha mask
        alpha = img_array[:, :, 3]
        opaque_mask = alpha > self.alpha_threshold

        # Get opaque pixels for clustering
        rgb_pixels = img_array[opaque_mask][:, :3].astype(np.float64)

        if len(rgb_pixels) == 0:
            log.warning("No opaque pixels found in image")
            return {"layers": [], "output_dir": str(out_dir), "layer_count": 0}

        # Determine actual k (can't have more clusters than pixels)
        actual_k = min(self.k_clusters, len(rgb_pixels))

        # Run K-means or fallback
        if HAS_SKLEARN and actual_k >= 3:
            labels_flat = self._sklearn_kmeans(rgb_pixels, actual_k)
        else:
            labels_flat = self._simple_quantization(rgb_pixels, actual_k)

        # Reconstruct label map
        label_map = np.full((h, w), -1, dtype=np.int32)
        label_map[opaque_mask] = labels_flat

        # Calculate cluster colors (centers)
        unique_labels = sorted(set(labels_flat.tolist()))
        cluster_colors: Dict[int, np.ndarray] = {}
        for label in unique_labels:
            mask = labels_flat == label
            if mask.any():
                cluster_colors[label] = rgb_pixels[mask].mean(axis=0).astype(np.uint8)

        # Remove tiny layers (noise)
        min_pixels = int(h * w * self.min_layer_area_ratio)
        layer_info: List[Dict] = []
        for label in unique_labels:
            pixel_count = int(np.sum(label_map == label))
            if pixel_count < min_pixels:
                continue
            layer_info.append({
                "label": int(label),
                "pixel_count": pixel_count,
                "color": tuple(int(c) for c in cluster_colors.get(label, np.array([128, 128, 128]))),
            })

        # Sort by area (largest first)
        layer_info.sort(key=lambda x: x["pixel_count"], reverse=True)

        # Export layers as PNG files
        exported_layers: List[Dict] = []
        for idx, info in enumerate(layer_info):
            label = info["label"]
            layer_arr = np.zeros((h, w, 4), dtype=np.uint8)
            mask = label_map == label
            layer_arr[mask] = img_array[mask]
            layer_path = out_dir / f"layer_{idx:03d}.png"
            Image.fromarray(layer_arr, "RGBA").save(layer_path)
            info["path"] = str(layer_path)
            info["index"] = idx
            info["name"] = f"layer_{idx:03d}"
            exported_layers.append(info)

        # Save preview
        preview_path = out_dir / "preview.png"
        image.save(preview_path)

        # Generate layer guide
        guide_path = self._write_guide(out_dir, exported_layers, image.size)

        log.success(f"Created {len(exported_layers)} layers in {out_dir}")

        return {
            "layers": exported_layers,
            "output_dir": str(out_dir),
            "preview_path": str(preview_path),
            "guide_path": str(guide_path),
            "layer_count": len(exported_layers),
            "k_clusters": actual_k,
        }

    def layer_to_standard_parts(
        self,
        layer_result: Dict,
        image: Image.Image,
    ) -> Dict[str, np.ndarray]:
        """Map K-means cluster layers to standard semantic Live2D parts.

        Re-opens each exported layer PNG, computes actual pixel centroid and
        mean color, then assigns it to one of the standard part names using
        position + color heuristics.

        Args:
            layer_result: The dict returned by :meth:`layer`.
            image: The original (or optimized) image used to produce the
                layers — used only for sizing fallback.

        Returns:
            Dict mapping standard part name -> boolean ``(H, W)`` mask.
        """
        # Standard parts we will attempt to fill
        std_parts = [
            "hair_back", "hair_front", "face", "eyebrows",
            "eyes_left", "eyes_right", "nose", "mouth", "neck",
            "clothes_top", "clothes_bottom", "arms", "hands",
            "legs", "accessories",
        ]
        parts: Dict[str, np.ndarray] = {}

        layers = layer_result.get("layers", [])
        if not layers:
            return parts

        # Determine canvas size from first layer
        first_path = layers[0].get("path")
        if first_path and Path(first_path).is_file():
            with Image.open(first_path) as f:
                w, h = f.size
        else:
            w, h = image.size

        # Build per-cluster masks and centroid stats directly from PNGs
        cluster_stats: List[Dict] = []
        for info in layers:
            path = info.get("path")
            if not path or not Path(path).is_file():
                continue
            with Image.open(path) as f:
                arr = np.array(f.convert("RGBA"))
            mask = arr[:, :, 3] > 0
            if not mask.any():
                continue
            ys, xs = np.where(mask)
            rgb = arr[mask][:, :3].astype(np.float32)
            cluster_stats.append({
                "mask": mask,
                "area": int(mask.sum()),
                "area_ratio": float(mask.sum()) / float(h * w),
                "cx": float(xs.mean()) / w,
                "cy": float(ys.mean()) / h,
                "mean_rgb": rgb.mean(axis=0),
                "h_span": float((ys.max() - ys.min()) / h),
                "w_span": float((xs.max() - xs.min()) / w),
            })

        cluster_stats.sort(key=lambda d: d["area"], reverse=True)

        # Guess face bounding box from skin-toned clusters.
        face_bbox = self._guess_face_region(cluster_stats, w, h)
        fx0, fy0, fx1, fy1 = face_bbox
        fy_top = fy0 / h
        fy_bot = fy1 / h

        def add(name: str, m: np.ndarray) -> None:
            if name not in std_parts:
                name = "accessories"
            parts[name] = parts.get(name, np.zeros((h, w), dtype=bool)) | m

        fcx = (fx0 + fx1) / 2.0 / w

        for cs in cluster_stats:
            name = self._classify_cluster(cs, (fx0, fy0, fx1, fy1), w, h)
            if name == "eye":
                target = "eyes_left" if cs["cx"] < fcx else "eyes_right"
                add(target, cs["mask"])
            elif name == "eyebrow":
                add("eyebrows", cs["mask"])
            elif name in std_parts:
                add(name, cs["mask"])
            else:
                add("accessories", cs["mask"])

        # Ensure both hair_front and hair_back exist
        if "hair_back" not in parts and "hair_front" in parts:
            parts["hair_back"] = parts["hair_front"].copy()
        if "hair_front" not in parts and "hair_back" in parts:
            parts["hair_front"] = parts["hair_back"].copy()
        return {k: v for k, v in parts.items() if v.any()}

    # --------------------------------------------------------------- internal

    def _sklearn_kmeans(self, pixels: np.ndarray, k: int) -> np.ndarray:
        """Run sklearn KMeans clustering on an ``(N, 3)`` float64 array."""
        log.debug(f"Running sklearn KMeans with k={k}, n_pixels={len(pixels)}")
        max_pixels = 100_000
        if len(pixels) > max_pixels:
            indices = np.random.choice(len(pixels), max_pixels, replace=False)
            sample_pixels = pixels[indices]
        else:
            sample_pixels = pixels
            indices = None

        kmeans = SKLearnKMeans(n_clusters=k, random_state=42, n_init=10, max_iter=100)
        kmeans.fit(sample_pixels)
        labels = kmeans.predict(pixels) if indices is not None else kmeans.labels_
        return labels.astype(np.int32)

    def _simple_quantization(self, pixels: np.ndarray, k: int) -> np.ndarray:
        """Fallback color quantization when sklearn is not available."""
        log.debug(f"Using simple quantization, k={k}")
        bits = max(2, int(np.log2(max(8, k) ** (1 / 3))))
        factor = 256 // (2 ** bits)
        quantized = (pixels // factor) * factor
        _, inverse = np.unique(quantized, axis=0, return_inverse=True)
        return inverse.astype(np.int32)

    def _write_guide(self, out_dir: Path, layers: List[Dict], size: Tuple[int, int]) -> str:
        """Write a human-readable layer guide text file."""
        guide_path = out_dir / "LAYER_GUIDE.txt"
        w, h = size
        with open(guide_path, "w", encoding="utf-8") as f:
            f.write("Live2D Master Agent v9.0 - K-Means Layer Guide\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Image size: {w}x{h}\n")
            f.write(f"Clusters (k): {self.k_clusters}\n")
            f.write(f"Layers exported: {len(layers)}\n\n")
            f.write("Layers (by area, largest first):\n")
            f.write("-" * 60 + "\n")
            for layer in layers:
                r, g, b = layer["color"]
                f.write(f"  {layer['index']:3d}. layer_{layer['index']:03d}.png  "
                        f"pixels={layer['pixel_count']:>8,}  "
                        f"color=RGB({r:3d},{g:3d},{b:3d})\n")
            f.write("\nImporting into Live2D Cubism Editor:\n")
            f.write("-" * 60 + "\n")
            f.write("1. Open Cubism Editor\n")
            f.write("2. File -> Import PSD or import layers as textures\n")
            f.write("3. Arrange layers from back to front\n")
            f.write("4. Create ArtMeshes for each part\n")
            f.write("5. Set up deformation parameters\n")
        return str(guide_path)

    # -------------------------------------------------- semantic mapping utils

    @staticmethod
    def _is_skin(rgb: np.ndarray) -> bool:
        """Loose anime skin-tone test."""
        r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
        return (
            r > 140 and g > 90 and b > 70
            and r >= g and r >= b and (r - g) > 8
        )

    def _guess_face_region(
        self,
        stats: List[Dict],
        w: int,
        h: int,
    ) -> Tuple[int, int, int, int]:
        """Estimate face bbox ``(x0,y0,x1,y1)`` from cluster stats."""
        best = None
        best_score = -1e9
        for cs in stats:
            if not self._is_skin(cs["mean_rgb"]):
                continue
            if cs["area_ratio"] < 0.02 or cs["area_ratio"] > 0.5:
                continue
            score = -abs(cs["cy"] - 0.35) + cs["area_ratio"]
            if score > best_score:
                best_score = score
                best = cs
        if best is not None:
            ys, xs = np.where(best["mask"])
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            pad_x = int((x1 - x0) * 0.15)
            pad_y = int((y1 - y0) * 0.15)
            return (max(0, x0 - pad_x), max(0, y0 - pad_y),
                    min(w - 1, x1 + pad_x), min(h - 1, y1 + pad_y))
        return (int(w * 0.25), int(h * 0.18), int(w * 0.75), int(h * 0.65))

    def _classify_cluster(
        self,
        cs: Dict,
        face_bbox: Tuple[int, int, int, int],
        w: int,
        h: int,
    ) -> str:
        """Classify a single cluster using position/color heuristics."""
        fx0, fy0, fx1, fy1 = face_bbox
        cx, cy = cs["cx"], cs["cy"]
        rgb = cs["mean_rgb"]
        ar = cs["area_ratio"]

        fy_top = fy0 / h
        fy_bot = fy1 / h
        rel_y = (cy - fy_top) / max(1e-6, fy_bot - fy_top)
        on_face_x = (fx0 / w) <= cx <= (fx1 / w)

        # Eye / brow / nose / mouth relative to face
        if 0.25 < rel_y < 0.6 and on_face_x and 0.0005 < ar < 0.05:
            return "eye"
        if 0.05 < rel_y < 0.35 and on_face_x and ar < 0.02 and cs["h_span"] < 0.1:
            return "eyebrow"
        if 0.45 < rel_y < 0.75 and on_face_x and ar < 0.008:
            return "nose"
        if 0.65 < rel_y < 1.0 and on_face_x and ar < 0.02:
            return "mouth"

        # Face
        if ar > 0.03 and self._is_skin(rgb) and on_face_x and 0.2 < cy < 0.7:
            return "face"
        # Neck
        if cy > fy_bot and self._is_skin(rgb) and cs["w_span"] < 0.25 and ar < 0.08:
            return "neck"
        # Hands/arms (skin away from face)
        if self._is_skin(rgb):
            if cy > 0.55 and cs["w_span"] < 0.12 and ar < 0.04:
                return "hands"
            if 0.45 < cy < 0.9 and ar < 0.15:
                return "arms"
        # Legs
        if cy > 0.7 and cs["h_span"] > cs["w_span"] and ar < 0.25:
            return "legs"
        # Hair
        if (cy < 0.45 or ar > 0.15) and not self._is_skin(rgb) and ar > 0.01:
            if on_face_x and cy < (fy_top + fy_bot) / 2 and ar < 0.3:
                return "hair_front"
            return "hair_back"
        # Clothes
        if ar > 0.05 and cy > 0.45:
            return "clothes_top" if cy < 0.7 else "clothes_bottom"
        return "other"


def layer_image_file(
    input_path: str,
    output_dir: Optional[str] = None,
    k_clusters: int = 12,
) -> Dict:
    """Convenience function: layer an image file and export PNG layers.

    P0-3: Uses :class:`KMeansLayerer` (v6) by default.
    """
    img = Image.open(input_path).convert("RGBA")
    if output_dir is None:
        p = Path(input_path)
        output_dir = str(p.parent / f"{p.stem}_layers")
    layerer = KMeansLayerer(k_clusters=k_clusters)
    return layerer.layer(img, output_dir=output_dir)


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(description="Live2D K-Means Layer Tool v6 (Default)")
    parser.add_argument("input", help="Input image path")
    parser.add_argument("output", nargs="?", default=None, help="Output directory")
    parser.add_argument("--k", type=int, default=12, help="Number of clusters (default: 12)")
    args = parser.parse_args()

    result = layer_image_file(args.input, args.output, k_clusters=args.k)
    print(f"\nLayering complete: {result['layer_count']} layers in {result['output_dir']}")
