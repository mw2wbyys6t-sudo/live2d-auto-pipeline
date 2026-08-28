#!/usr/bin/env python3
"""
Live2D Master Agent - Semantic Segmentation Engine

Segments an anime character image into the 18 standard Live2D parts using
one of several backends (ISNet/anime-segmentation, SAM, or rembg), with
graceful fallbacks to a HSV color-based heuristic when no learned model is
available. Returns a dict mapping part name -> boolean mask.

The 18 standard parts (see :attr:`SemanticSegmenter.STANDARD_PARTS`):
    hair_back, hair_front, face, eyebrows, eyes_left, eyes_right, nose,
    mouth, neck, clothes_top, clothes_bottom, arms, hands, legs,
    accessories, plus background/skin/other as helpers.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

from core.logger import get_logger

log = get_logger("segment.semantic")

# Optional dependencies
try:
    import cv2  # type: ignore
    HAS_CV2 = True
except ImportError:  # pragma: no cover
    HAS_CV2 = False

try:
    import torch  # type: ignore
    HAS_TORCH = True
except ImportError:  # pragma: no cover
    HAS_TORCH = False

try:
    import rembg  # type: ignore
    HAS_REMBG = True
except ImportError:  # pragma: no cover
    HAS_REMBG = False


class SemanticSegmenter:
    """Semantic segmentation of anime character images into Live2D parts.

    Args:
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``.
        model_type: Backend selector:
            - ``"isnet"`` (default) — SkyTNT/anime-segmentation style ISNet
            - ``"sam"`` — Segment Anything (supports anime-finetuned weights)
            - ``"rembg"`` — rembg/U2Net (fast, foreground only; parts derived
              from heuristics)
    """

    STANDARD_PARTS: List[str] = [
        "hair_back",
        "hair_front",
        "face",
        "eyebrows",
        "eyes_left",
        "eyes_right",
        "nose",
        "mouth",
        "neck",
        "clothes_top",
        "clothes_bottom",
        "arms",
        "hands",
        "legs",
        "accessories",
    ]

    # Mapping from rough semantic region (hair/skin/eyes/etc.) to Live2D
    # parts. SAM/ISNet typically produce per-component masks that are then
    # classified by position+color+shape into these names.
    PART_PRIORITY: List[str] = [
        "eyes_left", "eyes_right", "eyebrows", "nose", "mouth",
        "face", "hair_front", "hair_back", "neck",
        "hands", "arms", "clothes_top", "clothes_bottom", "legs",
        "accessories",
    ]

    def __init__(self, device: str = "auto", model_type: str = "isnet") -> None:
        self.model_type = (model_type or "isnet").lower()
        self.device = self._resolve_device(device)
        self._model = None
        self._model_loaded = False
        log.info(f"SemanticSegmenter initialized (backend={self.model_type}, device={self.device})")

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device == "auto":
            if HAS_TORCH and torch.cuda.is_available():  # type: ignore[name-defined]
                return "cuda"
            return "cpu"
        return device

    # ------------------------------------------------------------------ public

    def segment(self, image: Image.Image) -> Dict[str, np.ndarray]:
        """Segment ``image`` into per-part boolean masks.

        Args:
            image: PIL Image (any mode; converted to RGBA internally).

        Returns:
            Dict mapping part name -> ``np.uint8`` or boolean mask.
            Only parts with non-empty masks are included.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        img_arr = np.array(image)
        h, w = img_arr.shape[:2]

        # Try the configured model first.
        masks: List[Dict] = []
        self._last_was_fallback = False
        try:
            if self.model_type == "isnet":
                masks = self._segment_isnet(image)
            elif self.model_type == "sam":
                masks = self._segment_sam(image)
            elif self.model_type == "rembg":
                masks = self._segment_rembg(image)
            else:
                log.warning(f"Unknown model_type '{self.model_type}'; falling back")
        except Exception as exc:
            log.warning(f"Backend '{self.model_type}' failed: {exc}")

        if not masks:
            log.info("No learned masks produced; using HSV color fallback")
            self._last_was_fallback = True
            return self._fallback_color_segment(image)

        try:
            parts = self._classify_masks_anime(masks, np.array(image.convert("RGB")))
        except Exception as exc:
            log.warning(f"Mask classification failed ({exc}); using fallback")
            return self._fallback_color_segment(image)

        # Validate shapes
        clean: Dict[str, np.ndarray] = {}
        for name, m in parts.items():
            if m is None:
                continue
            m_arr = np.asarray(m, dtype=bool)
            if m_arr.shape != (h, w):
                log.debug(f"Resizing mask '{name}' from {m_arr.shape} to {(h, w)}")
                m_pil = Image.fromarray(m_arr.astype(np.uint8) * 255, "L").resize((w, h), Image.NEAREST)
                m_arr = np.array(m_pil) > 0
            if m_arr.any():
                clean[name] = m_arr
        # If classification produced nothing useful, fall back.
        if not clean:
            return self._fallback_color_segment(image)
        return clean

    def layer(
        self,
        image: Image.Image,
        output_dir: Optional[str] = None,
        label_layers: bool = True,
    ) -> Dict:
        """Segment ``image`` into Live2D parts and export RGBA layer PNGs.

        This is the adapter method used by :class:`core.workflow.WorkflowEngine`
        to make the semantic segmenter interchangeable with
        :class:`core.segment_engine.kmeans.KMeansLayerer`. It calls
        :meth:`segment` to obtain boolean masks, then uses
        :class:`core.segment_engine.composer.LayerComposer` to perform
        amodal completion, RGBA extraction, and PNG export, and finally
        returns a dict whose shape matches ``KMeansLayerer.layer()``.

        Args:
            image: Source PIL Image (any mode; converted to RGBA).
            output_dir: Directory to write layer PNGs + preview + guide.
                Falls back to a timestamped ``layers_<ts>`` directory.
            label_layers: Accepted for API parity; currently unused.

        Returns:
            Dict with keys: ``success``, ``method``, ``layers`` (list of
            dicts with ``index``/``name``/``path``/``pixel_count``/``size``),
            ``output_dir``, ``preview_path``, ``composite_preview``,
            ``layer_count``, ``k_clusters``, ``segmentation_mask``.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        if output_dir:
            out = Path(output_dir)
        else:
            out = Path.cwd() / "output" / f"layers_{int(time.time())}"
        out.mkdir(parents=True, exist_ok=True)

        # 1. Run segmentation -> boolean masks per part
        t0 = time.time()
        masks = self.segment(image)
        is_fallback = getattr(self, "_last_was_fallback", False)
        method = "semantic_hsv_fallback" if is_fallback else "semantic"

        if not masks:
            log.warning("Semantic segmentation produced no masks; returning empty result")
            return {
                "success": False,
                "method": method,
                "layers": [],
                "output_dir": str(out),
                "preview_path": None,
                "composite_preview": None,
                "layer_count": 0,
                "k_clusters": 0,
                "segmentation_mask": None,
            }

        # 2. Use LayerComposer to do amodal completion + ordered PNG export
        try:
            from core.segment_engine.composer import LayerComposer
            composer = LayerComposer(device=self.device)
            composed = composer.compose(masks, image, str(out))
            ordered = composer.reorder_layers(composed)
        except Exception as exc:
            log.warning(f"LayerComposer failed ({exc}); falling back to direct mask export")
            ordered = self._direct_mask_export(masks, image, out)

        # 3. Convert to KMeans-compatible layer list
        exported_layers: List[Dict] = []
        for idx, (part_name, info) in enumerate(ordered.items()):
            exported_layers.append({
                "index": idx,
                "name": part_name,
                "part_name": part_name,
                "part_name_en": part_name,
                "path": info["path"],
                "size": image.size,
                "pixel_count": info.get("pixel_count", 0),
                "label": idx,
                "color": info.get("mean_color", (128, 128, 128)),
                "mean_color": info.get("mean_color", (128, 128, 128)),
                "bbox": info.get("bbox", (0, 0, 0, 0)),
                "amodal_completed": bool(info.get("completed", False)),
            })

        # 4. Save preview (original optimized image) and composite preview
        preview_path = out / "preview.png"
        image.save(preview_path)

        composite_preview_path = out / "composite_preview.png"
        self._write_composite_preview(exported_layers, image.size, composite_preview_path)

        # 5. Write layer JSON guide via composer
        guide_path = out / "layer_guide.json"
        try:
            composer_path_cls = None
            from core.segment_engine.composer import LayerComposer as _LC
            composer_path_cls = _LC
            if composer_path_cls is not None and "composed" in dir():
                composer.generate_layer_json(composed if composed else ordered, str(guide_path))
        except Exception:
            pass

        elapsed = time.time() - t0
        log.success(
            f"Semantic layering complete: {len(exported_layers)} layers "
            f"(method={method}) in {elapsed:.2f}s -> {out}"
        )

        return {
            "success": True,
            "method": method,
            "layers": exported_layers,
            "output_dir": str(out),
            "preview_path": str(preview_path),
            "composite_preview": str(composite_preview_path),
            "layer_count": len(exported_layers),
            "k_clusters": 0,
            "segmentation_mask": None,
            "guide_path": str(guide_path) if guide_path.exists() else None,
        }

    def _direct_mask_export(
        self,
        masks: Dict[str, np.ndarray],
        image: Image.Image,
        out: Path,
    ) -> Dict[str, Dict]:
        """Fallback: directly burn each boolean mask into a transparent PNG.

        Used when LayerComposer / amodal completion raises (e.g. missing
        optional deps). Returns the same dict shape as
        :meth:`LayerComposer.compose`.
        """
        from collections import OrderedDict
        img_arr = np.array(image.convert("RGBA"))
        h, w = img_arr.shape[:2]
        ordered_names = [n for n in self.STANDARD_PARTS if n in masks] + \
                        sorted(n for n in masks if n not in self.STANDARD_PARTS)
        result: "OrderedDict[str, Dict]" = OrderedDict()
        for idx, name in enumerate(ordered_names):
            mask = np.asarray(masks[name], dtype=bool)
            layer_arr = np.zeros_like(img_arr)
            layer_arr[mask] = img_arr[mask]
            path = out / f"{name}.png"
            Image.fromarray(layer_arr, "RGBA").save(path)
            pixels = layer_arr[..., 3] > 0
            ys, xs = np.where(pixels)
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else (0, 0, 0, 0)
            mean_color = tuple(int(c) for c in layer_arr[pixels][:, :3].mean(axis=0)) if pixels.any() else (0, 0, 0)
            result[name] = {
                "name": name,
                "path": str(path),
                "bbox": bbox,
                "size": (int(bbox[2] - bbox[0] + 1), int(bbox[3] - bbox[1] + 1)),
                "pixel_count": int(pixels.sum()),
                "mean_color": mean_color,
                "occluded_by": [],
                "completed": False,
            }
        return result

    @staticmethod
    def _write_composite_preview(
        layers: List[Dict],
        size: tuple,
        path: Path,
    ) -> None:
        """Alpha-composite all exported layers onto a transparent canvas."""
        composite = Image.new("RGBA", size, (0, 0, 0, 0))
        for info in layers:
            try:
                layer_img = Image.open(info["path"]).convert("RGBA")
                composite = Image.alpha_composite(composite, layer_img)
            except Exception:
                continue
        composite.save(path)

    # --------------------------------------------------------------- backends

    def _load_isnet(self):
        """Load an ISNet-based anime segmentation model.

        Tries ``anime_segmentation`` package first, then a generic ISNet via
        ``rembg`` with the ``isnet-general-use`` session. Sets
        ``self._model`` and ``self._model_loaded``.
        """
        if self._model_loaded:
            return
        self._model_loaded = True

        # 1) Prefer the dedicated anime-segmentation ISNet if available
        try:  # pragma: no cover - heavy optional dep
            from anime_segmentation import get_isnet_model  # type: ignore
            self._model = get_isnet_model(device=self.device)
            log.success("Loaded ISNet model from anime_segmentation package")
            return
        except ImportError:
            log.debug("anime_segmentation package not available")
        except Exception as exc:
            log.warning(f"anime_segmentation load failed: {exc}")

        # 2) Fall back to rembg with ISNet session (foreground mask only)
        if HAS_REMBG:
            try:  # pragma: no cover - optional
                from rembg import new_session  # type: ignore
                self._model = new_session("isnet-general-use", providers=[self._onnx_provider()])
                log.success("Loaded ISNet via rembg (foreground-only)")
                return
            except Exception as exc:
                log.warning(f"rembg ISNet session failed: {exc}")

        log.warning(
            "No ISNet backend available. High-quality semantic segmentation "
            "will be disabled; falling back to HSV color segmentation "
            "(quality ≈ K-means). To enable ISNet, install rembg with "
            "`pip install rembg[cpu]` (the isnet-general-use model will be "
            "downloaded automatically on first use), or install the dedicated "
            "anime-segmentation package for anime-optimized masks."
        )
        self._model = None

    def _onnx_provider(self) -> str:
        return "CUDAExecutionProvider" if self.device == "cuda" else "CPUExecutionProvider"

    def _load_sam(self):
        """Load a Segment Anything model, preferring anime-finetuned weights."""
        if self._model_loaded:
            return
        self._model_loaded = True
        if not HAS_TORCH:
            log.warning("torch not installed; cannot load SAM")
            return
        try:  # pragma: no cover - heavy optional dep
            from segment_anything import sam_model_registry, SamAutomaticMaskGenerator  # type: ignore
            import glob
            ckpt = None
            patterns = [
                os.path.expanduser("~/.cache/huggingface/hub/models--anime-segmentation--sam-vit-huge-anime/snapshots/*/sam_vit_h_anime.pth"),
                os.path.expanduser("~/.cache/anime-segmentation/sam_vit_h_anime.pth"),
                os.path.expanduser("~/models/sam_vit_h_anime.pth"),
            ]
            for pat in patterns:
                m = glob.glob(pat)
                if m:
                    ckpt = m[0]
                    break
            model_type = "vit_h"
            if ckpt is None:
                # Try vanilla ViT-B checkpoint from segment-anything
                for alt in ["sam_vit_b_01ec64.pth", "sam_vit_l_0b3195.pth"]:
                    cand = os.path.expanduser(f"~/.cache/sam/{alt}")
                    if os.path.isfile(cand):
                        ckpt = cand
                        model_type = "vit_b" if "vit_b" in alt else "vit_l"
                        break
            if ckpt is None:
                log.warning("No SAM checkpoint found; attempting download-less default is not possible")
                self._model = None
                return
            sam = sam_model_registry[model_type](checkpoint=ckpt)
            sam.to(device=self.device)
            self._model = SamAutomaticMaskGenerator(sam)
            log.success(f"Loaded SAM ({model_type}) from {ckpt} on {self.device}")
        except ImportError:
            log.warning("segment_anything not installed")
            self._model = None
        except Exception as exc:
            log.warning(f"SAM load failed: {exc}")
            self._model = None

    # -------------------------------------------------------- backend runners

    def _segment_isnet(self, image: Image.Image) -> List[Dict]:
        """Run ISNet segmentation, returning SAM-style mask dicts."""
        self._load_isnet()
        if self._model is None:
            return []
        rgb = np.array(image.convert("RGB"))
        # If we got an anime-segmentation ISNet with a predict() method, use it
        if hasattr(self._model, "predict"):
            try:  # pragma: no cover - external API
                pred = self._model.predict(rgb)
                # Expect dict label -> mask (H,W) bool; convert to list form
                if isinstance(pred, dict):
                    return [{"segmentation": np.asarray(m).astype(bool), "label": str(k)}
                            for k, m in pred.items() if np.asarray(m).any()]
            except Exception as exc:
                log.warning(f"ISNet predict failed: {exc}")
        # rembg ISNet session -> single foreground mask
        if HAS_REMBG and self._model is not None:
            try:  # pragma: no cover - optional
                from rembg import remove  # type: ignore
                cutout = remove(image, session=self._model)
                if cutout.mode != "RGBA":
                    cutout = cutout.convert("RGBA")
                fg = (np.array(cutout)[:, :, 3] > 0).astype(bool)
                if fg.any():
                    return [{"segmentation": fg, "label": "foreground"}]
            except Exception as exc:
                log.warning(f"rembg ISNet inference failed: {exc}")
        return []

    def _segment_sam(self, image: Image.Image) -> List[Dict]:
        """Run SAM automatic mask generation."""
        self._load_sam()
        if self._model is None:
            return []
        rgb = np.array(image.convert("RGB"))
        try:  # pragma: no cover - external
            masks = self._model.generate(rgb)
            # SAM masks already have: segmentation (bool HxW), bbox, area, etc.
            return masks
        except Exception as exc:
            log.warning(f"SAM inference failed: {exc}")
            return []

    def _segment_rembg(self, image: Image.Image) -> List[Dict]:
        """Run rembg foreground extraction (returns a single mask)."""
        if not HAS_REMBG:
            return []
        try:  # pragma: no cover - optional
            cutout = rembg.remove(image)  # type: ignore[union-attr]
            if cutout.mode != "RGBA":
                cutout = cutout.convert("RGBA")
            fg = (np.array(cutout)[:, :, 3] > 0).astype(bool)
            return [{"segmentation": fg, "label": "foreground"}] if fg.any() else []
        except Exception as exc:
            log.warning(f"rembg inference failed: {exc}")
            return []

    # -------------------------------------------------------- classification

    def _classify_masks_anime(
        self,
        masks: List[Dict],
        image: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Classify raw SAM/ISNet masks into the 15 anime-part set.

        Uses position (centroid), color (mean HSV/RGB), shape (aspect, area),
        and simple geometric constraints. Left/right eyes and eyebrows are
        split by x-centroid.
        """
        h, w = image.shape[:2]
        parts: Dict[str, np.ndarray] = {p: np.zeros((h, w), dtype=bool) for p in self.STANDARD_PARTS}

        # Sort masks by area descending (large regions first)
        norm_masks: List[Dict] = []
        for m in masks:
            seg = np.asarray(m.get("segmentation"), dtype=bool)
            if seg.shape != (h, w):
                # resize if necessary
                seg_pil = Image.fromarray(seg.astype(np.uint8) * 255, "L").resize((w, h), Image.NEAREST)
                seg = np.array(seg_pil) > 0
            if not seg.any():
                continue
            area = int(seg.sum())
            ys, xs = np.where(seg)
            cx = float(xs.mean()) / w
            cy = float(ys.mean()) / h
            mean_rgb = image[seg].mean(axis=0) if area else np.array([128, 128, 128])
            norm_masks.append({
                "seg": seg,
                "area": area,
                "area_ratio": area / float(h * w),
                "cx": cx,
                "cy": cy,
                "mean_rgb": mean_rgb,
                "bbox": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                "h_span": (ys.max() - ys.min()) / h,
                "w_span": (xs.max() - xs.min()) / w,
            })
        norm_masks.sort(key=lambda d: d["area"], reverse=True)

        if not norm_masks:
            return {}

        # Estimate face bounding box: the mask closest to vertical center that
        # has skin-toned colors.
        face_region = self._guess_face_region(norm_masks, image)
        fx0, fy0, fx1, fy1 = face_region
        face_mask = np.zeros((h, w), dtype=bool)
        face_mask[fy0:fy1 + 1, fx0:fx1 + 1] = True

        for m in norm_masks:
            name = self._classify_single(m, face_region, image)
            if name in ("eyes_left", "eyes_right", "eyebrows"):
                # split left/right by x centroid relative to face center
                fcx = (fx0 + fx1) / 2.0 / w
                if name == "eyebrows":
                    target = "eyebrows"  # both brows share a mask but add individually
                    parts[target] |= m["seg"]
                    continue
                if m["cx"] < fcx:
                    target = "eyes_left" if name == "eye" else "eyebrows_left"
                else:
                    target = "eyes_right" if name == "eye" else "eyebrows_right"
                if target == "eyebrows_left":
                    parts["eyebrows"] |= m["seg"]
                elif target == "eyebrows_right":
                    parts["eyebrows"] |= m["seg"]
                else:
                    parts[target] |= m["seg"]
                continue
            if name == "eye":
                # Unknown laterality
                target = "eyes_left" if m["cx"] < (fx0 + fx1) / 2.0 / w else "eyes_right"
                parts[target] |= m["seg"]
                continue
            if name in parts:
                parts[name] |= m["seg"]
            elif name == "background":
                continue
            else:
                # Lump unknown into accessories
                parts["accessories"] |= m["seg"]

        # If hair_back/hair_front not separated, split hair mask top vs bangs:
        if not parts["hair_back"].any() and parts["hair_front"].any():
            parts["hair_back"] = parts["hair_front"].copy()
        if not parts["hair_front"].any() and parts["hair_back"].any():
            parts["hair_front"] = parts["hair_back"].copy()

        # Strip empty parts
        return {k: v for k, v in parts.items() if v.any()}

    def _classify_single(
        self,
        m: Dict,
        face_region: tuple,
        image: np.ndarray,
    ) -> str:
        """Heuristically label one mask with a part name."""
        fx0, fy0, fx1, fy1 = face_region
        fh = max(1, fy1 - fy0)
        fw = max(1, fx1 - fx0)
        cx, cy = m["cx"], m["cy"]
        ar = m["area_ratio"]
        rgb = m["mean_rgb"]
        brightness = float(rgb.mean())

        # Eye-like: small, near vertical center of face, on face, colorful/white/black
        on_face_x = fx0 / max(1, image.shape[1]) <= cx <= fx1 / max(1, image.shape[1])
        on_face_y = fy0 / max(1, image.shape[0]) <= cy <= fy1 / max(1, image.shape[0])
        # Normalize cx/cy to face coords
        face_h = image.shape[0]
        face_w = image.shape[1]
        fy_center = (fy0 + fy1) / 2.0 / face_h
        # Eyes: ~10-30% down from top of face
        rel_y = (cy - fy0 / face_h) / max(1e-6, (fy1 - fy0) / face_h)

        # Mouth: low in face area, small
        if 0.65 < rel_y < 1.0 and on_face_x and ar < 0.02 and ar > 0.0005:
            return "mouth"
        # Nose: tiny, middle of face
        if 0.45 < rel_y < 0.75 and on_face_x and ar < 0.008:
            return "nose"
        # Eyes: around 0.3-0.55 down from face top, small-ish
        if 0.25 < rel_y < 0.6 and on_face_x and 0.0005 < ar < 0.05:
            return "eye"
        # Eyebrows: above eyes
        if 0.05 < rel_y < 0.35 and on_face_x and ar < 0.02 and m["h_span"] < 0.1:
            return "eyebrows"
        # Face: large skin-toned region overlapping face bbox
        if ar > 0.03 and self._is_skin_color(rgb) and on_face_x and 0.2 < cy < 0.7:
            return "face"

        # Neck: below face, narrow, skin color
        if cy > fy1 / face_h and self._is_skin_color(rgb) and m["w_span"] < 0.25 and ar < 0.08:
            return "neck"

        # Hair: top portion or surrounding face; non-skin; large
        if (cy < 0.45 or ar > 0.15) and not self._is_skin_color(rgb) and ar > 0.01:
            # Hair front = overlaps upper face; hair back = above/around
            if on_face_x and cy < fy_center and ar < 0.3:
                return "hair_front"
            return "hair_back"

        # Hands/arms: skin color away from face
        if self._is_skin_color(rgb):
            if cy > 0.55 and m["w_span"] < 0.12 and ar < 0.04:
                return "hands"
            if 0.45 < cy < 0.9 and ar < 0.15:
                return "arms"

        # Legs: lower third, elongated vertical, skin or dark
        if cy > 0.7 and m["h_span"] > m["w_span"] and ar < 0.25:
            return "legs"

        # Clothes: large, non-skin, middle-to-lower body
        if ar > 0.05 and cy > 0.45:
            if cy < 0.7:
                return "clothes_top"
            return "clothes_bottom"

        # Small non-skin features near hair are accessories
        if ar < 0.02 and brightness > 150:
            return "accessories"

        return "other"

    @staticmethod
    def _is_skin_color(rgb: np.ndarray) -> bool:
        """Very loose anime skin-tone test in RGB."""
        r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
        if r < 120 or g < 80 or b < 70:
            return False
        if r < g or r < b:
            return False
        if (r - g) < 8:
            return False
        return True

    def _guess_face_region(self, masks: List[Dict], image: np.ndarray) -> tuple:
        """Estimate face bbox (x0,y0,x1,y1) in pixel coordinates.

        Picks the largest skin-toned mask near the vertical center; falls
        back to a centered 40%x50% box.
        """
        h, w = image.shape[:2]
        best = None
        best_score = -1.0
        for m in masks:
            if not self._is_skin_color(m["mean_rgb"]):
                continue
            if m["area_ratio"] < 0.02 or m["area_ratio"] > 0.5:
                continue
            cx, cy = m["cx"], m["cy"]
            # Face typically between y 0.2 and 0.5
            score = -abs(cy - 0.35) + m["area_ratio"]
            if score > best_score:
                best_score = score
                best = m
        if best is not None:
            x0, y0, x1, y1 = best["bbox"]
            # Pad bbox a touch
            pad_x = int((x1 - x0) * 0.15)
            pad_y = int((y1 - y0) * 0.15)
            return (max(0, x0 - pad_x), max(0, y0 - pad_y),
                    min(w - 1, x1 + pad_x), min(h - 1, y1 + pad_y))
        # Fallback: centered box
        return (int(w * 0.25), int(h * 0.18), int(w * 0.75), int(h * 0.65))

    # ---------------------------------------------------------- color fallback

    def _fallback_color_segment(self, image: Image.Image) -> Dict[str, np.ndarray]:
        """HSV-based color segmentation fallback when no model is available.

        Splits the foreground (opaque if RGBA with transparency, or all
        pixels) into broad regions: hair (top dark/saturated band), face
        (skin ellipse in upper-middle), eyes/mouth (small dark features),
        clothes (lower-body non-skin), etc. This is crude but produces valid
        boolean masks for downstream processing.
        """
        rgb = np.array(image.convert("RGB"))
        h, w = rgb.shape[:2]

        # Determine foreground: if image has alpha, use it; else assume full fg
        if image.mode == "RGBA":
            alpha = np.array(image)[:, :, 3]
            fg = alpha > 10
        else:
            fg = np.ones((h, w), dtype=bool)

        # Build HSV for color tests
        if HAS_CV2:
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        else:
            hsv = self._rgb_to_hsv(rgb)

        R, G, B = rgb[:, :, 0].astype(int), rgb[:, :, 1].astype(int), rgb[:, :, 2].astype(int)

        skin = (R > 140) & (G > 90) & (B > 70) & (R > G) & (R > B) & ((R - G) > 8) & fg
        # Hair: saturated/dark pixels above face band OR non-skin high saturation
        if HAS_CV2:
            sat = hsv[:, :, 1]
            val = hsv[:, :, 2]
        else:
            sat = hsv[:, :, 1]
            val = hsv[:, :, 2]
        yy, xx = np.mgrid[0:h, 0:w]
        cy_norm = yy / max(1, h - 1)
        upper = cy_norm < 0.55
        hair_color = (sat > 60) & (val < 230) & ~skin
        hair = (hair_color & upper) & fg

        # Face: largest connected skin region in upper-middle
        face_zone = (cy_norm > 0.18) & (cy_norm < 0.6)
        face_seed = skin & face_zone
        face = self._largest_component(face_seed)

        # Neck: skin below face
        face_bottom = int(np.where(face)[0].max()) if face.any() else int(h * 0.5)
        neck_band = (yy > face_bottom) & (yy < face_bottom + h * 0.12)
        neck = skin & neck_band
        # restrict to central column
        cx_est = int(np.median(np.where(face)[1])) if face.any() else w // 2
        neck &= (np.abs(xx - cx_est) < w * 0.12)

        # Clothes: non-skin below mid
        lower = cy_norm > 0.45
        clothes_color = ~skin & (val > 40) & lower
        clothes = clothes_color & fg
        clothes_top = clothes & (cy_norm < 0.75)
        clothes_bottom = clothes & (cy_norm >= 0.75)

        # Arms/hands: skin outside face/neck
        skin_body = skin & ~face & ~neck
        arms = skin_body & (cy_norm < 0.85)
        hands = skin_body & (cy_norm > 0.7) & (cy_norm < 0.95)
        # Limit hands to small side regions
        legs = skin & (cy_norm > 0.85)

        # Eyes/mouth/nose/eyebrows: within face, dark/saturated small clusters
        # We approximate by splitting face horizontally into bands.
        if face.any():
            fy0 = int(np.where(face)[0].min())
            fy1 = int(np.where(face)[0].max())
        else:
            fy0, fy1 = int(h * 0.2), int(h * 0.55)
        fx0 = int(np.where(face)[1].min()) if face.any() else int(w * 0.3)
        fx1 = int(np.where(face)[1].max()) if face.any() else int(w * 0.7)

        # Bands (relative to face height)
        face_h = max(1, fy1 - fy0)
        eye_lo = fy0 + int(face_h * 0.28)
        eye_hi = fy0 + int(face_h * 0.5)
        brow_hi = fy0 + int(face_h * 0.25)
        brow_lo = fy0 + int(face_h * 0.12)
        nose_lo = fy0 + int(face_h * 0.5)
        nose_hi = fy0 + int(face_h * 0.72)
        mouth_lo = fy0 + int(face_h * 0.72)
        mouth_hi = fy0 + int(face_h * 0.95)

        eye_band = (yy >= eye_lo) & (yy <= eye_hi) & (xx >= fx0) & (xx <= fx1)
        dark_pix = (R < 120) & (G < 120) & (B < 140)
        eyes_all = eye_band & dark_pix & face
        # Split by x relative to face center
        fcx = (fx0 + fx1) // 2
        eyes_left_mask = eyes_all & (xx < fcx)
        eyes_right_mask = eyes_all & (xx >= fcx)

        brow_band = (yy >= brow_lo) & (yy <= brow_hi) & (xx >= fx0) & (xx <= fx1)
        eyebrows = brow_band & dark_pix & fg

        nose_band = (yy >= nose_lo) & (yy <= nose_hi) & (xx >= fx0) & (xx <= fx1)
        nose_mask = nose_band & dark_pix & face

        mouth_band = (yy >= mouth_lo) & (yy <= mouth_hi) & (xx >= fx0) & (xx <= fx1)
        mouth = mouth_band & ((R > 120) & (R < 220) & (G < 120) & (B < 130)) & face

        # Hair split: back = above and behind face; front = bangs overlapping face
        hair_back = hair & ~((xx >= fx0) & (xx <= fx1) & (yy >= fy0) & (yy <= fy0 + face_h * 0.3))
        hair_front = hair & ~hair_back
        # Ensure non-empty hair_front/hair_back
        if not hair_front.any() and hair.any():
            hair_front = hair.copy()
        if not hair_back.any() and hair.any():
            hair_back = hair.copy()

        out = {
            "hair_back": hair_back & fg,
            "hair_front": hair_front & fg,
            "face": face,
            "eyebrows": eyebrows,
            "eyes_left": eyes_left_mask,
            "eyes_right": eyes_right_mask,
            "nose": nose_mask,
            "mouth": mouth,
            "neck": neck,
            "clothes_top": clothes_top,
            "clothes_bottom": clothes_bottom,
            "arms": arms,
            "hands": hands,
            "legs": legs,
            "accessories": np.zeros((h, w), dtype=bool),
        }
        return {k: v for k, v in out.items() if v.any()}

    # ----------------------------------------------------------- numpy helpers

    @staticmethod
    def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
        """Vectorized RGB->HSV in pure numpy (returns H:0-180, S:0-255, V:0-255 to mimic cv2)."""
        rgb_f = rgb.astype(np.float32) / 255.0
        r, g, b = rgb_f[..., 0], rgb_f[..., 1], rgb_f[..., 2]
        mx = np.max(rgb_f, axis=-1)
        mn = np.min(rgb_f, axis=-1)
        v = mx
        df = mx - mn
        s = np.where(mx == 0, 0, df / np.maximum(mx, 1e-6))
        # Hue
        rc = (mx - r) / np.maximum(df, 1e-6)
        gc = (mx - g) / np.maximum(df, 1e-6)
        bc = (mx - b) / np.maximum(df, 1e-6)
        h = np.zeros_like(mx)
        h = np.where((mx == r) & (df > 0), (bc - gc), h)
        h = np.where((mx == g) & (df > 0), 2.0 + rc - bc, h)
        h = np.where((mx == b) & (df > 0), 4.0 + gc - rc, h)
        h = (h / 6.0) % 1.0
        # To cv2 scale: H*180 (since H 0-360 in open cv represented as 0-180)
        return np.stack([(h * 180.0).astype(np.uint8),
                         (s * 255.0).astype(np.uint8),
                         (v * 255.0).astype(np.uint8)], axis=-1)

    @staticmethod
    def _largest_component(mask: np.ndarray) -> np.ndarray:
        """Return the largest connected component of a boolean mask."""
        if not mask.any():
            return mask
        if HAS_CV2:
            num, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
            if num <= 1:
                return mask
            # label 0 is background
            sizes = stats[1:, cv2.CC_STAT_AREA]
            largest = int(np.argmax(sizes)) + 1
            return labels == largest
        # Pure-numpy BFS fallback (simple, acceptable for fallback only)
        h, w = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        best_mask = np.zeros_like(mask)
        best_size = 0
        from collections import deque
        ys, xs = np.where(mask & ~visited)
        for y0, x0 in zip(ys.tolist(), xs.tolist()):
            if visited[y0, x0]:
                continue
            dq = deque([(y0, x0)])
            visited[y0, x0] = True
            comp = [(y0, x0)]
            while dq:
                cy, cx = dq.popleft()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            dq.append((ny, nx))
                            comp.append((ny, nx))
            if len(comp) > best_size:
                best_size = len(comp)
                best_mask.fill(False)
                for yy, xx in comp:
                    best_mask[yy, xx] = True
        return best_mask


# Backward-compat alias used by older imports (e.g. semantic.layer_image_file)
SemanticLayerer = SemanticSegmenter


def segment_image_file(path: str, model_type: str = "auto") -> Dict[str, np.ndarray]:
    """Convenience: segment an image file.

    Args:
        path: Path to image.
        model_type: Backend (``"auto"`` picks isnet then fallback).

    Returns:
        Dict of part name -> boolean mask.
    """
    img = Image.open(path).convert("RGBA")
    seg = SemanticSegmenter(model_type="isnet" if model_type == "auto" else model_type)
    return seg.segment(img)


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(description="Live2D Semantic Segmentation")
    parser.add_argument("input", help="Input image path")
    parser.add_argument("--model", default="auto", choices=["auto", "isnet", "sam", "rembg"])
    args = parser.parse_args()
    result = segment_image_file(args.input, model_type=args.model)
    print(f"Segmented {len(result)} parts:")
    for name, m in result.items():
        print(f"  {name:16s} pixels={int(m.sum()):>8d}")
