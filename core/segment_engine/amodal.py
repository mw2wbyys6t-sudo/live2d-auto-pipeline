#!/usr/bin/env python3
"""
Live2D Master Agent - Amodal Completion

Fills in occluded regions (e.g. hair behind the face, clothes behind arms)
so that extracted layers remain complete as standalone textures for Live2D
rigging. Primary method is OpenCV inpainting; falls back to nearest-neighbor
color fill if OpenCV is unavailable, and exposes a stub for a learned
pix2gestalt-style model.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

from core.logger import get_logger

log = get_logger("segment.amodal")

try:
    import cv2  # type: ignore
    HAS_CV2 = True
except ImportError:  # pragma: no cover
    HAS_CV2 = False
    log.debug("opencv-python not available; amodal completion will use nearest-neighbor fill")

try:
    import torch  # type: ignore  # noqa: F401
    HAS_TORCH = True
except ImportError:  # pragma: no cover
    HAS_TORCH = False


class AmodalCompleter:
    """Complete occluded regions of character layers.

    Given the visible (known) pixels and a mask of the occluded region that
    *should* be filled in, this class produces a plausible completion. Used
    when extracting layers such as ``hair_back`` (partially hidden by the
    face) or ``clothes`` (partially hidden by arms).
    """

    def __init__(self, device: str = "auto") -> None:
        """Initialize the completer.

        Args:
            device: Torch device string (``"auto"``, ``"cpu"``, ``"cuda"``).
                Only meaningful for the learned pix2gestalt stub.
        """
        self.device = self._resolve_device(device)
        self._pix2gestalt = None  # lazily loaded
        log.debug(f"AmodalCompleter ready (cv2={HAS_CV2}, torch={HAS_TORCH}, device={self.device})")

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device == "auto":
            if HAS_TORCH:
                import torch  # type: ignore
                return "cuda" if torch.cuda.is_available() else "cpu"
            return "cpu"
        return device

    # ------------------------------------------------------------------ public

    def complete(
        self,
        image: Image.Image,
        visible_mask: np.ndarray,
        occluded_mask: np.ndarray,
    ) -> Image.Image:
        """Fill in occluded regions of ``image``.

        Args:
            image: Full original image (RGB or RGBA). The visible content
                outside ``occluded_mask`` is preserved.
            visible_mask: Boolean ``(H, W)`` array marking known/visible pixels
                of the part being completed. May be used as a hint.
            occluded_mask: Boolean ``(H, W)`` array marking pixels that need
                to be hallucinated for this part.

        Returns:
            RGBA PIL image with the occluded region filled in. The alpha
            channel is set to opaque on the union of visible + completed area.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image)
        h, w = arr.shape[:2]

        vis = np.asarray(visible_mask, dtype=bool)
        occ = np.asarray(occluded_mask, dtype=bool)
        if vis.shape != (h, w):
            raise ValueError(f"visible_mask shape {vis.shape} != image shape {(h, w)}")
        if occ.shape != (h, w):
            raise ValueError(f"occluded_mask shape {occ.shape} != image shape {(h, w)}")

        # Nothing to fill
        if not occ.any():
            log.debug("complete: no occluded pixels; returning image unchanged")
            return image

        target_pixels = vis | occ
        rgb = arr[:, :, :3].copy()

        inpaint_mask = occ.astype(np.uint8) * 255

        filled: Optional[np.ndarray] = None

        # 1) Try pix2gestalt learned model if available
        try:
            filled = self._inpaint_pix2gestalt(Image.fromarray(rgb, "RGB"), occ)
            if filled is not None:
                rgb = np.array(filled.convert("RGB"))
        except Exception as exc:  # pragma: no cover - model/runtime
            log.warning(f"pix2gestalt inpainting failed ({exc}); trying cv2")

        # 2) OpenCV Telea inpainting on RGB
        if filled is None and HAS_CV2:
            try:
                rgb = self._inpaint_cv2(rgb, inpaint_mask)
            except Exception as exc:
                log.warning(f"OpenCV inpainting failed ({exc}); using simple fill")
                rgb = self._simple_fill(rgb, occ)
        elif filled is None and not HAS_CV2:
            rgb = self._simple_fill(rgb, occ)

        # Composite: keep original visible pixels, take filled for occluded
        out_rgb = arr[:, :, :3].copy()
        out_rgb[occ] = rgb[occ]

        out_alpha = np.where(target_pixels, 255, 0).astype(np.uint8)
        out = np.dstack([out_rgb, out_alpha])
        return Image.fromarray(out, "RGBA")

    # --------------------------------------------------------------- internal

    def _inpaint_cv2(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Inpaint using OpenCV's Telea algorithm.

        Args:
            image: RGB uint8 array of shape ``(H, W, 3)``.
            mask: uint8 array (H, W) with 255 marking areas to fill.

        Returns:
            Inpainted RGB uint8 array.
        """
        # cv2 expects BGR
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        radius = max(3, min(15, int(round(min(image.shape[:2]) * 0.02))))
        result_bgr = cv2.inpaint(bgr, mask, radius, cv2.INPAINT_TELEA)
        return cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)

    def _inpaint_pix2gestalt(
        self,
        image: Image.Image,
        occluded_mask: np.ndarray,
    ) -> Optional[Image.Image]:
        """Stub for a learned pix2gestalt-style amodal completion model.

        This hook exists so that future integration of a generative inpainter
        does not require changing call sites. It currently returns ``None``
        (meaning "not available") unless a ``_pix2gestalt`` object has been
        injected with a callable ``__call__(image, mask) -> Image``.

        Args:
            image: RGB source image.
            occluded_mask: Boolean mask of pixels to fill.

        Returns:
            Filled RGB PIL Image or ``None`` if no model is available.
        """
        if self._pix2gestalt is None:
            # Try to load gracefully
            try:  # pragma: no cover - optional integration
                import importlib  # noqa: F401 (placeholder for future model)
                # from somemodule import Pix2Gestalt  # type: ignore
                # self._pix2gestalt = Pix2Gestalt(device=self.device)
            except Exception:
                return None
            if self._pix2gestalt is None:
                return None

        try:  # pragma: no cover - optional integration
            mask_img = Image.fromarray((occluded_mask.astype(np.uint8) * 255), "L")
            result = self._pix2gestalt(image, mask_img)  # type: ignore[misc]
            return result.convert("RGB") if hasattr(result, "convert") else result
        except Exception as exc:
            log.warning(f"pix2gestalt call failed: {exc}")
            return None

    def _simple_fill(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Nearest-neighbor color fill — ultimate fallback.

        Iteratively dilates known pixels into the masked region using a
        3x3 max-filter over distance. Produces a soft color-bleed fill rather
        than hard edges.

        Args:
            image: RGB uint8 array ``(H, W, 3)``.
            mask: Boolean array ``(H, W)`` (True = fill).

        Returns:
            RGB uint8 array with masked pixels filled.
        """
        h, w = image.shape[:2]
        known = ~mask
        # If we have no known pixels, just return mid-gray fill
        if not known.any():
            out = image.copy()
            out[mask] = (128, 128, 128)
            return out

        result = image.copy().astype(np.float32)

        if HAS_CV2:
            # Use cv2.distanceTransform to find nearest known pixel for each
            # masked pixel and copy its color via repeated dilation blending.
            try:
                inv_mask = (~known).astype(np.uint8)
                # Repeatedly dilate known region inward to propagate color
                cur_known = known.copy()
                iter_img = result.copy()
                kernel = np.ones((3, 3), np.uint8)
                while not cur_known.all():
                    dilated = cv2.dilate(cur_known.astype(np.uint8), kernel, iterations=1).astype(bool)
                    new_pixels = dilated & ~cur_known
                    if not new_pixels.any():
                        break
                    # Average of 3x3 known neighbors
                    sum_rgb = cv2.boxFilter(iter_img, ddepth=-1, ksize=(3, 3), normalize=False)
                    cnt = cv2.boxFilter(cur_known.astype(np.float32), ddepth=-1, ksize=(3, 3), normalize=False)
                    cnt = np.maximum(cnt, 1.0)
                    avg = sum_rgb / cnt[..., None]
                    iter_img[new_pixels] = avg[new_pixels]
                    cur_known = dilated
                return np.clip(iter_img, 0, 255).astype(np.uint8)
            except Exception as exc:
                log.warning(f"cv2-based simple_fill failed ({exc}); using pure numpy")

        # Pure numpy fallback: iterative 3x3 neighborhood-average dilations.
        # Each pass extends known coverage by one pixel with the local mean.
        cur = result.copy()
        cur_known = known.copy()
        max_iters = max(h, w)
        for _ in range(max_iters):
            if cur_known.all():
                break
            # Compute 3x3 sum over known values and known-count
            padded = np.pad(cur, ((1, 1), (1, 1), (0, 0)), mode="edge")
            padded_known = np.pad(cur_known, 1, mode="constant", constant_values=False)
            neighbor_sum = np.zeros_like(cur, dtype=np.float32)
            neighbor_cnt = np.zeros((h, w), dtype=np.float32)
            for dy in range(3):
                for dx in range(3):
                    if dy == 1 and dx == 1:
                        # center handled below
                        continue
                    slab = padded[dy:dy + h, dx:dx + w]
                    kslab = padded_known[dy:dy + h, dx:dx + w]
                    neighbor_sum[kslab] += slab[kslab]
                    neighbor_cnt[kslab] += 1.0
            # Also include center if known
            center_known = cur_known
            neighbor_sum[center_known] += cur[center_known]
            neighbor_cnt[center_known] += 1.0
            new_pixels = (~cur_known) & (neighbor_cnt > 0)
            if not new_pixels.any():
                break
            avg = neighbor_sum[new_pixels] / neighbor_cnt[new_pixels, None]
            cur[new_pixels] = avg
            cur_known |= new_pixels
        # Any still-unknown pixels get neutral gray
        if not cur_known.all():
            cur[~cur_known] = (128, 128, 128)
        return cur.astype(np.uint8)
