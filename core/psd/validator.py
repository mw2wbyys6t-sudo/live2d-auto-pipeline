#!/usr/bin/env python3
"""
Live2D Master Agent - PSD Validator
Validates PSD files against Live2D Cubism Editor standards.
"""

import os
from pathlib import Path
from typing import Dict, List, Any

from core.logger import get_logger
from core.psd.parser import PSDParser, PSDValidationError

log = get_logger("psd_validator")

# Live2D PSD requirements
LIVE2D_PSD_REQUIREMENTS = {
    "min_height_px": 1000,
    "max_height_px": 8192,
    "min_width_px": 500,
    "max_width_px": 8192,
    "color_mode": "RGB",
    "bit_depth": 8,
    "max_layers": 2000,
    "dpi": 72,
}


class PSDValidator:
    """Validates PSD files against Live2D Cubism Editor import requirements."""

    # P2-2 FIX: Stable issue IDs (deterministic, not random)
    ISSUE_CODES = {
        "E_SIZE_SMALL": "Canvas too small (min 500x1000px for Live2D)",
        "E_SIZE_LARGE": "Canvas too large (max 8192x8192px)",
        "E_COLOR_MODE": "Color mode must be RGB",
        "E_BIT_DEPTH": "Bit depth must be 8-bit",
        "E_EMPTY": "PSD has no layers",
        "W_FEW_LAYERS": "Too few layers for full Live2D rig (< 10)",
        "W_MANY_LAYERS": "Very high layer count may cause performance issues",
        "W_HIDDEN_LAYERS": "Hidden layers found (may be intentional)",
        "I_LAYER_COUNT": "Layer count information",
    }

    def __init__(self):
        self.parser = PSDParser()

    def validate(self, filepath: str) -> Dict[str, Any]:
        """Validate a PSD file for Live2D import compatibility.

        Returns dict with: valid (bool), score (0-100), issues (list), metadata (dict)
        """
        issues = []
        score = 100

        try:
            data = self.parser.parse(filepath)
        except PSDValidationError as e:
            return {
                "valid": False,
                "score": 0,
                "issues": [{"id": "E_SECURITY", "severity": "error", "message": str(e)}],
                "metadata": {},
            }
        except Exception as e:
            return {
                "valid": False,
                "score": 0,
                "issues": [{"id": "E_PARSE", "severity": "error", "message": f"Parse error: {e}"}],
                "metadata": {},
            }

        w = data["width"]
        h = data["height"]
        req = LIVE2D_PSD_REQUIREMENTS

        # Size checks
        if h < req["min_height_px"] or w < req["min_width_px"]:
            issues.append(self._make_issue("E_SIZE_SMALL", "error",
                f"Canvas {w}x{h}px is below minimum {req['min_width_px']}x{req['min_height_px']}px"))
            score -= 30
        elif h > req["max_height_px"] or w > req["max_width_px"]:
            issues.append(self._make_issue("E_SIZE_LARGE", "error",
                f"Canvas {w}x{h}px exceeds maximum {req['max_width_px']}x{req['max_height_px']}px"))
            score -= 30
        else:
            # Optimal size: 2000-4000px height
            if req["min_height_px"] <= h <= 8000:
                pass
            else:
                issues.append(self._make_issue("I_LAYER_COUNT", "info",
                    f"Canvas height {h}px is outside optimal 2000-4000px range"))

        # Color mode check
        color_mode = data.get("color_mode", "")
        if "RGB" not in str(color_mode):
            issues.append(self._make_issue("E_COLOR_MODE", "error",
                f"Color mode is {color_mode}, must be RGB"))
            score -= 25

        # Bit depth
        depth = data.get("depth", 8)
        if depth != 8:
            issues.append(self._make_issue("E_BIT_DEPTH", "error",
                f"Bit depth is {depth}-bit, must be 8-bit"))
            score -= 20

        # Layer count
        layer_count = data.get("layer_count") or len(data.get("layers", []))
        if layer_count == 0:
            issues.append(self._make_issue("E_EMPTY", "error", "PSD contains no layers"))
            score -= 25
        elif layer_count < 10:
            issues.append(self._make_issue("W_FEW_LAYERS", "warning",
                f"Only {layer_count} layers; full Live2D rig requires 20+ layers"))
            score -= 10
        elif layer_count > 500:
            issues.append(self._make_issue("W_MANY_LAYERS", "warning",
                f"{layer_count} layers may cause performance issues in Cubism Editor"))
            score -= 5

        # Check for hidden layers
        hidden = [l for l in data.get("layers", []) if not l.get("visible", True)]
        if hidden:
            issues.append(self._make_issue("W_HIDDEN_LAYERS", "warning",
                f"{len(hidden)} hidden layer(s) found"))

        score = max(0, min(100, score))

        return {
            "valid": score >= 60,
            "score": score,
            "issues": issues,
            "metadata": {
                "filepath": filepath,
                "width": w,
                "height": h,
                "color_mode": color_mode,
                "depth": depth,
                "layer_count": layer_count,
            },
        }

    def _make_issue(self, code: str, severity: str, message: str) -> Dict:
        """P2-2 FIX: Create an issue with STABLE deterministic ID."""
        # Issue ID is the code itself (stable), not a random UUID
        return {
            "id": code,
            "severity": severity,
            "message": message,
            "description": self.ISSUE_CODES.get(code, ""),
        }
