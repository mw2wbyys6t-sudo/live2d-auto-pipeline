#!/usr/bin/env python3
"""
Live2D Master Agent - QA Engine (P2-2 FIXED: stable issue IDs)

P2-2: Issue IDs are deterministic (hash-based) and do not change between runs.
Quality assessment for generated images and PSDs against Live2D standards.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

from PIL import Image
import numpy as np

from core.logger import get_logger

log = get_logger("qa")


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class QAIssue:
    """P2-2 FIXED: Stable issue ID derived from issue code + context."""
    code: str
    severity: Severity
    message: str
    context: str = ""
    score_penalty: int = 0

    @property
    def id(self) -> str:
        """Stable deterministic ID: hash of code + context."""
        key = f"{self.code}:{self.context}"
        return f"QA-{hashlib.sha256(key.encode()).hexdigest()[:10]}"

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "context": self.context,
            "score_penalty": self.score_penalty,
        }


@dataclass
class QAResult:
    """Result of a quality assessment."""
    valid: bool
    score: int
    issues: List[QAIssue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "valid": self.valid,
            "score": self.score,
            "issues": [i.to_dict() for i in self.issues],
            "metrics": self.metrics,
        }


class QAEngine:
    """Quality assessment for Live2D character images and PSDs.

    Evaluates:
    1. Canvas size (30% weight)
    2. Edge clarity (30% weight)
    3. Color separation (20% weight)
    4. Format compliance (20% weight)
    """

    # Weight configuration
    WEIGHTS = {
        "canvas_size": 0.30,
        "edge_clarity": 0.30,
        "color_separation": 0.20,
        "format": 0.20,
    }

    # P2-2 FIXED: Well-known issue codes (stable across versions)
    ISSUE_CODES = {
        "E001": "Image too small for Live2D",
        "E002": "Image too large (may cause Cubism Editor issues)",
        "E003": "Blurry edges detected",
        "E004": "Too many colors (hard to separate layers)",
        "E005": "Not RGB/RGBA format",
        "W001": "Image size not optimal (recommend 2000-4000px height)",
        "W002": "Background not transparent/solid (may complicate cutout)",
        "W003": "Low edge clarity",
        "I001": "Image passes basic checks",
    }

    def assess_image(self, image: Image.Image) -> QAResult:
        """Assess a PIL Image for Live2D suitability.

        Returns QAResult with score 0-100 and list of issues.
        """
        issues = []
        metrics = {}
        score = 100

        w, h = image.size
        metrics["width"] = w
        metrics["height"] = h
        metrics["mode"] = image.mode

        # 1. Canvas size check (30% weight)
        size_score = self._check_canvas_size(w, h, issues)
        metrics["canvas_size_score"] = size_score

        # 2. Edge clarity (30% weight)
        edge_score = self._check_edge_clarity(image, issues)
        metrics["edge_clarity_score"] = edge_score

        # 3. Color separation (20% weight)
        color_score = self._check_colors(image, issues)
        metrics["color_separation_score"] = color_score

        # 4. Format check (20% weight)
        fmt_score = self._check_format(image, issues)
        metrics["format_score"] = fmt_score

        # Weighted total
        score = int(
            size_score * self.WEIGHTS["canvas_size"] +
            edge_score * self.WEIGHTS["edge_clarity"] +
            color_score * self.WEIGHTS["color_separation"] +
            fmt_score * self.WEIGHTS["format"]
        )
        score = max(0, min(100, score))

        # Apply direct penalties
        for issue in issues:
            score = max(0, score - issue.score_penalty)

        valid = score >= 60
        metrics["overall_score"] = score

        if valid:
            issues.append(QAIssue(
                code="I001",
                severity=Severity.INFO,
                message=f"Image passes QA with score {score}/100",
                score_penalty=0,
            ))

        return QAResult(valid=valid, score=score, issues=issues, metrics=metrics)

    def assess_image_file(self, filepath: str) -> QAResult:
        """Assess an image file."""
        img = Image.open(filepath).convert('RGBA')
        return self.assess_image(img)

    def _check_canvas_size(self, w: int, h: int, issues: List[QAIssue]) -> int:
        """Check canvas dimensions. Returns score 0-100."""
        score = 100
        min_h, max_h = 1000, 8000
        opt_h_min, opt_h_max = 2000, 4000

        if h < min_h:
            issues.append(QAIssue("E001", Severity.ERROR,
                f"Height {h}px below minimum {min_h}px for Live2D",
                context=f"canvas_{w}x{h}", score_penalty=20))
            score = 30
        elif h > max_h:
            issues.append(QAIssue("E002", Severity.ERROR,
                f"Height {h}px exceeds maximum {max_h}px",
                context=f"canvas_{w}x{h}", score_penalty=20))
            score = 30
        elif h < opt_h_min or h > opt_h_max:
            issues.append(QAIssue("W001", Severity.WARNING,
                f"Height {h}px not in optimal range {opt_h_min}-{opt_h_max}px",
                context=f"canvas_{w}x{h}", score_penalty=5))
            score = 70

        return score

    def _check_edge_clarity(self, image: Image.Image, issues: List[QAIssue]) -> int:
        """Check edge clarity using Sobel filter. Returns score 0-100."""
        score = 100
        try:
            arr = np.array(image.convert('L'), dtype=np.float64)
            # Simple Sobel-like edge detection
            from scipy.ndimage import sobel
            edges = np.hypot(sobel(arr, axis=0), sobel(arr, axis=1))
            edge_density = float(np.mean(edges > 30))

            if edge_density < 0.01:
                issues.append(QAIssue("E003", Severity.ERROR,
                    "Very low edge density - image may be blurry",
                    context=f"edge_density_{edge_density:.4f}", score_penalty=20))
                score = 40
            elif edge_density < 0.03:
                issues.append(QAIssue("W003", Severity.WARNING,
                    "Low edge clarity - outlines may need sharpening",
                    context=f"edge_density_{edge_density:.4f}", score_penalty=10))
                score = 65

        except ImportError:
            # scipy not available - use simple gradient check
            arr = np.array(image.convert('L'), dtype=np.float64)
            grad_x = np.abs(np.diff(arr, axis=1)).mean() if arr.shape[1] > 1 else 0
            grad_y = np.abs(np.diff(arr, axis=0)).mean() if arr.shape[0] > 1 else 0
            avg_grad = (grad_x + grad_y) / 2
            if avg_grad < 5:
                issues.append(QAIssue("W003", Severity.WARNING,
                    "Low contrast/edges detected (simple check)",
                    context=f"gradient_{avg_grad:.1f}", score_penalty=5))
                score = 70

        return score

    def _check_colors(self, image: Image.Image, issues: List[QAIssue]) -> int:
        """Check color count/separation for layerability. Returns score 0-100."""
        score = 100
        img_small = image.convert('RGB').resize((128, 128), Image.LANCZOS)
        arr = np.array(img_small).reshape(-1, 3)
        # Quantize to count dominant colors
        quantized = (arr // 32) * 32
        unique_colors = len(np.unique(quantized, axis=0))

        if unique_colors > 200:
            issues.append(QAIssue("E004", Severity.WARNING,
                f"High color count ({unique_colors} unique) - layer separation may be poor",
                context=f"colors_{unique_colors}", score_penalty=15))
            score = 50
        elif unique_colors > 100:
            issues.append(QAIssue("W002" if False else "W003", Severity.WARNING,
                f"Moderate color count ({unique_colors}) - consider color quantization",
                context=f"colors_{unique_colors}", score_penalty=5))
            score = 75

        # Check for non-transparent/non-white background
        if image.mode == 'RGBA':
            alpha = np.array(image.split()[-1])
            transparent_ratio = float((alpha < 10).mean())
            bg_pixels = arr[:20, :20]  # corner sample
            bg_is_white = np.mean(bg_pixels) > 240
            if transparent_ratio < 0.1 and not bg_is_white:
                issues.append(QAIssue("W002", Severity.WARNING,
                    "Background is not transparent or pure white - cutout may be needed",
                    context=f"alpha_ratio_{transparent_ratio:.2f}", score_penalty=5))

        return score

    def _check_format(self, image: Image.Image, issues: List[QAIssue]) -> int:
        """Check image format. Returns score 0-100."""
        score = 100
        if image.mode not in ('RGB', 'RGBA'):
            issues.append(QAIssue("E005", Severity.ERROR,
                f"Image mode is {image.mode}, must be RGB or RGBA",
                context=f"mode_{image.mode}", score_penalty=15))
            score = 50
        return score
