#!/usr/bin/env python3
"""
Live2D Master Agent - Visual Embedding Extraction

Provides CLIP-based or histogram-based visual embedding extraction with
lazy model loading and graceful degradation when heavy ML dependencies
are not installed.
"""

import math
from typing import List, Optional

from PIL import Image

from core.logger import get_logger

log = get_logger("character.embedding")


class EmbeddingExtractor:
    """Extract visual embeddings from images.

    Supports two backends:
    - ``clip`` (default): Uses openai/clip-vit-base-patch32 via the
      ``transformers`` library. Produces a 512-dimensional embedding.
      Falls back to histogram if transformers/torch are unavailable.
    - ``histogram``: A pure-numpy color histogram + edge histogram that
      produces a 256-dimensional embedding and works without any ML
      dependencies.
    """

    def __init__(self, model_name: str = "clip") -> None:
        """Initialize the extractor.

        Args:
            model_name: Either ``"clip"`` or ``"histogram"``. The CLIP
                backend is loaded lazily on first call to :meth:`extract`.
        """
        self.model_name: str = model_name
        self._model = None
        self._processor = None
        self._model_loaded: bool = False
        self._clip_attempted: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, image: Image.Image) -> List[float]:
        """Extract a visual embedding from *image*.

        Args:
            image: A PIL Image (any mode; will be converted to RGB).

        Returns:
            A list of floats (512-dim for CLIP, 256-dim for histogram).
        """
        if self.model_name == "clip":
            embedding = self._extract_clip(image)
            if embedding is not None:
                return embedding
            log.info("CLIP unavailable, falling back to histogram embedding")
        return self._extract_histogram(image)

    def similarity(self, emb1: List[float], emb2: List[float]) -> float:
        """Compute cosine similarity between two embeddings.

        Args:
            emb1: First embedding vector.
            emb2: Second embedding vector.

        Returns:
            Cosine similarity in [-1, 1]. Returns 0.0 if dimensions
            don't match.
        """
        if len(emb1) != len(emb2) or not emb1:
            return 0.0
        dot = sum(a * b for a, b in zip(emb1, emb2))
        n1 = math.sqrt(sum(a * a for a in emb1)) or 1.0
        n2 = math.sqrt(sum(b * b for b in emb2)) or 1.0
        return dot / (n1 * n2)

    # ------------------------------------------------------------------
    # CLIP backend
    # ------------------------------------------------------------------

    def _load_clip(self) -> bool:
        """Lazily load the CLIP model. Returns True on success."""
        if self._clip_attempted:
            return self._model_loaded
        self._clip_attempted = True

        try:
            import torch  # noqa: F401
            from transformers import CLIPModel, CLIPProcessor

            log.info("Loading CLIP model (openai/clip-vit-base-patch32)...")
            self._model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
            self._processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
            self._model.eval()
            self._model_loaded = True
            log.info("CLIP model loaded successfully")
            return True
        except ImportError as e:
            log.warning(f"CLIP dependencies not available: {e}")
            log.info("Install with: pip install torch transformers")
        except Exception as e:
            log.warning(f"Failed to load CLIP model: {e}")

        self._model_loaded = False
        return False

    def _extract_clip(self, image: Image.Image) -> Optional[List[float]]:
        """Extract a 512-dim CLIP image embedding.

        Returns None if the model could not be loaded.
        """
        if not self._load_clip():
            return None

        try:
            import torch

            rgb = image.convert("RGB")
            inputs = self._processor(images=rgb, return_tensors="pt")
            with torch.no_grad():
                features = self._model.get_image_features(**inputs)
            # Normalize
            features = features / features.norm(p=2, dim=-1, keepdim=True)
            return features.squeeze(0).cpu().tolist()
        except Exception as e:
            log.warning(f"CLIP extraction failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Histogram fallback
    # ------------------------------------------------------------------

    def _extract_histogram(self, image: Image.Image) -> List[float]:
        """Extract a 256-dim embedding using color + edge histograms.

        The embedding is composed of:
        - 192 bins: 64-bin color histogram per RGB channel
        - 64 bins: grayscale edge gradient histogram

        Args:
            image: A PIL Image.

        Returns:
            A 256-element list of floats, L2-normalized.
        """
        import numpy as np

        rgb = image.convert("RGB").resize((128, 128))
        arr = np.array(rgb, dtype=np.float32)

        # Per-channel color histograms (64 bins each = 192)
        hist_parts: List[List[float]] = []
        for ch in range(3):
            channel = arr[:, :, ch].astype(np.uint8)
            hist, _ = np.histogram(channel, bins=64, range=(0, 256))
            hist = hist.astype(np.float32)
            total = hist.sum() or 1.0
            hist_parts.append((hist / total).tolist())

        # Edge histogram (64 bins) from grayscale gradient magnitude
        gray = arr.mean(axis=2)
        gx = np.abs(np.diff(gray, axis=1))
        gy = np.abs(np.diff(gray, axis=0))
        # Pad to same shape
        gx = np.pad(gx, ((0, 0), (0, 1)), mode="constant")
        gy = np.pad(gy, ((0, 1), (0, 0)), mode="constant")
        grad = np.sqrt(gx ** 2 + gy ** 2)
        edge_hist, _ = np.histogram(grad, bins=64, range=(0, 256))
        edge_hist = edge_hist.astype(np.float32)
        total = edge_hist.sum() or 1.0
        edge_hist = (edge_hist / total).tolist()

        embedding: List[float] = []
        for h in hist_parts:
            embedding.extend(h)
        embedding.extend(edge_hist)

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in embedding)) or 1.0
        return [v / norm for v in embedding]

    # ------------------------------------------------------------------
    # Color features (helper / diagnostic)
    # ------------------------------------------------------------------

    def _extract_color_features(self, image: Image.Image) -> List[float]:
        """Extract dominant-color and channel statistics features.

        This is a lighter feature vector (15 dims) useful for quick
        comparisons or as a supplement to the histogram.

        Returns:
            A list of 15 floats: [mean_r, mean_g, mean_b, std_r, std_g,
            std_b, dominant_1_r/g/b, dominant_2_r/g/b, dominant_3_r/g/b].
        """
        import numpy as np

        rgb = image.convert("RGB").resize((64, 64))
        arr = np.array(rgb, dtype=np.float32).reshape(-1, 3)

        means = arr.mean(axis=0)
        stds = arr.std(axis=0)

        # Simple dominant colors via K-means (k=3) using sklearn-free approach
        # Use quantile-based approximation instead
        sorted_idx = np.argsort(arr[:, 0] * 65536 + arr[:, 1] * 256 + arr[:, 2])
        n = len(sorted_idx)
        dominants = []
        for frac in (0.1, 0.5, 0.9):
            idx = sorted_idx[min(int(n * frac), n - 1)]
            dominants.append(arr[idx])

        features: List[float] = []
        features.extend((means / 255.0).tolist())
        features.extend((stds / 255.0).tolist())
        for d in dominants:
            features.extend((d / 255.0).tolist())

        return features
