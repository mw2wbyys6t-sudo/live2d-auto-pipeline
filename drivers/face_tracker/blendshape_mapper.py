#!/usr/bin/env python3
"""
Map ARKit blendshape coefficients and facial landmarks to Live2D Cubism
parameters. Also provides smoothing and deadzone filtering to reduce
perceptual jitter.
"""

from typing import Dict, Optional, Tuple

from core.logger import get_logger

log = get_logger("blendshape_mapper")


class BlendShapeMapper:
    """Map ARKit blendshapes to Live2D parameters.

    Parameters
    ----------
    smoothing_factor : float
        Exponential smoothing factor (0..1). Higher = more smoothing.
    """

    # ARKit blendshape name -> Live2D parameter mapping.
    # Each entry maps one or more blendshape names to a Live2D param,
    # with a scale factor and whether the value is inverted.
    LANDMARK_TO_BLENDSHAPE_MAP: Dict[str, dict] = {
        # Eye blink (open = 1 - blink)
        "ParamEyeLOpen": {"blendshapes": ["eyeBlinkLeft"], "scale": 1.0, "invert": True},
        "ParamEyeROpen": {"blendshapes": ["eyeBlinkRight"], "scale": 1.0, "invert": True},
        # Mouth open from jaw
        "ParamMouthOpenY": {"blendshapes": ["jawOpen"], "scale": 1.0, "invert": False},
        # Mouth form: smile positive, frown negative
        "ParamMouthForm": {
            "blendshapes": ["mouthSmileLeft", "mouthSmileRight",
                            "mouthFrownLeft", "mouthFrownRight"],
            "scale": 1.0, "invert": False,
            "combine": "smile_frown",  # special combiner
        },
        # Brows
        "ParamBrowLY": {"blendshapes": ["browInnerUp", "browOuterUpLeft",
                                         "browDownLeft"],
                        "scale": 1.0, "invert": False, "combine": "brow"},
        "ParamBrowRY": {"blendshapes": ["browInnerUp", "browOuterUpRight",
                                         "browDownRight"],
                        "scale": 1.0, "invert": False, "combine": "brow"},
        # Cheek
        "ParamCheek": {"blendshapes": ["cheekPuff"], "scale": 1.0, "invert": False},
    }

    # Live2D parameter ranges (min, max, default)
    PARAMETER_RANGES: Dict[str, Tuple[float, float, float]] = {
        "ParamAngleX": (-30.0, 30.0, 0.0),
        "ParamAngleY": (-30.0, 30.0, 0.0),
        "ParamAngleZ": (-30.0, 30.0, 0.0),
        "ParamBodyAngleX": (-10.0, 10.0, 0.0),
        "ParamBodyAngleY": (-10.0, 10.0, 0.0),
        "ParamEyeLOpen": (0.0, 1.0, 1.0),
        "ParamEyeROpen": (0.0, 1.0, 1.0),
        "ParamEyeBallX": (-1.0, 1.0, 0.0),
        "ParamEyeBallY": (-1.0, 1.0, 0.0),
        "ParamMouthOpenY": (0.0, 1.0, 0.0),
        "ParamMouthForm": (-1.0, 1.0, 0.0),
        "ParamBrowLY": (-1.0, 1.0, 0.0),
        "ParamBrowRY": (-1.0, 1.0, 0.0),
        "ParamBreath": (0.0, 1.0, 0.5),
        "ParamCheek": (0.0, 1.0, 0.0),
        "ParamHairFrontX": (-15.0, 15.0, 0.0),
        "ParamHairBackX": (-15.0, 15.0, 0.0),
    }

    def __init__(self, smoothing_factor: float = 0.5):
        self.smoothing_factor = max(0.0, min(1.0, smoothing_factor))

    # ------------------------------------------------------------------
    # Main mapping
    # ------------------------------------------------------------------

    def map_to_live2d_params(
        self,
        blendshapes: Dict[str, float],
        landmarks: Optional[dict] = None,
    ) -> Dict[str, float]:
        """Convert ARKit blendshapes + optional head rotation to Live2D params.

        Parameters
        ----------
        blendshapes : dict[str, float]
            ARKit blendshapes (0..1).
        landmarks : dict, optional
            Raw landmark dict from ``FaceTracker.get_landmarks``. If provided
            with head rotation, angle parameters are set.

        Returns
        -------
        dict[str, float]
            Live2D parameter name -> value (already clamped to its range).
        """
        params: Dict[str, float] = {}

        for param_name, mapping in self.LANDMARK_TO_BLENDSHAPE_MAP.items():
            raw = self._combine_blendshapes(blendshapes, mapping)
            if mapping.get("invert"):
                raw = 1.0 - raw
            raw *= mapping.get("scale", 1.0)

            lo, hi, default = self.PARAMETER_RANGES.get(
                param_name, (0.0, 1.0, 0.0)
            )
            # Remap from 0..1 to parameter range
            value = lo + raw * (hi - lo)
            params[param_name] = max(lo, min(hi, value))

        # Eye look from gaze blendshapes
        eye_x = (
            blendshapes.get("eyeLookOutLeft", 0.0)
            - blendshapes.get("eyeLookInLeft", 0.0)
            + blendshapes.get("eyeLookInRight", 0.0)
            - blendshapes.get("eyeLookOutRight", 0.0)
        ) * 0.5
        eye_y = (
            blendshapes.get("eyeLookUpLeft", 0.0)
            + blendshapes.get("eyeLookUpRight", 0.0)
            - blendshapes.get("eyeLookDownLeft", 0.0)
            - blendshapes.get("eyeLookDownRight", 0.0)
        ) * 0.5
        params["ParamEyeBallX"] = max(-1.0, min(1.0, eye_x))
        params["ParamEyeBallY"] = max(-1.0, min(1.0, eye_y))

        # Head rotation from landmarks (if available via external computation)
        if landmarks and "head_rotation" in landmarks:
            rot = landmarks["head_rotation"]
            params["ParamAngleX"] = max(-30.0, min(30.0, rot.get("pitch", 0.0)))
            params["ParamAngleY"] = max(-30.0, min(30.0, rot.get("yaw", 0.0)))
            params["ParamAngleZ"] = max(-30.0, min(30.0, rot.get("roll", 0.0)))
        else:
            # Default head angles
            params.setdefault("ParamAngleX", 0.0)
            params.setdefault("ParamAngleY", 0.0)
            params.setdefault("ParamAngleZ", 0.0)

        # Body angle derived from head yaw (partial follow)
        params["ParamBodyAngleX"] = params.get("ParamAngleY", 0.0) * 0.3
        params["ParamBodyAngleY"] = params.get("ParamAngleX", 0.0) * 0.2

        # Breath default
        params.setdefault("ParamBreath", 0.5)

        return params

    # ------------------------------------------------------------------
    # Smoothing / deadzone
    # ------------------------------------------------------------------

    def smooth_parameters(
        self, current: Dict[str, float], previous: Dict[str, float]
    ) -> Dict[str, float]:
        """Exponential smoothing between previous and current parameters."""
        alpha = self.smoothing_factor
        smoothed: Dict[str, float] = {}
        keys = set(current.keys()) | set(previous.keys())
        for key in keys:
            cur = current.get(key)
            prev = previous.get(key)
            if cur is None:
                smoothed[key] = prev if prev is not None else 0.0
            elif prev is None:
                smoothed[key] = cur
            else:
                smoothed[key] = prev * alpha + cur * (1.0 - alpha)
        return smoothed

    def apply_deadzone(
        self, params: Dict[str, float], deadzone: float = 0.02
    ) -> Dict[str, float]:
        """Zero out parameter values within ``deadzone`` of their default.

        This prevents tiny jitter around the rest position from causing
        visible micro-movements.
        """
        result: Dict[str, float] = {}
        for key, value in params.items():
            lo, hi, default = self.PARAMETER_RANGES.get(
                key, (0.0, 1.0, 0.0)
            )
            # Normalize to -1..1 around default
            span = (hi - lo) if hi != lo else 1.0
            norm = (value - default) / span * 2.0
            if abs(norm) < deadzone:
                result[key] = default
            else:
                result[key] = value
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_parameter_ranges(self) -> Dict[str, Tuple[float, float, float]]:
        """Return ``{param_name: (min, max, default)}`` for all Live2D params."""
        return dict(self.PARAMETER_RANGES)

    def _combine_blendshapes(
        self, blendshapes: Dict[str, float], mapping: dict
    ) -> float:
        """Combine multiple ARKit blendshape inputs into a single 0..1 value."""
        combine = mapping.get("combine", "average")
        names = mapping["blendshapes"]
        values = [blendshapes.get(n, 0.0) for n in names]

        if combine == "average":
            return sum(values) / len(values) if values else 0.0
        elif combine == "max":
            return max(values) if values else 0.0
        elif combine == "smile_frown":
            # Smile blendshapes are first two, frown are last two
            smile_vals = [blendshapes.get(n, 0.0) for n in names[:2]]
            frown_vals = [blendshapes.get(n, 0.0) for n in names[2:]]
            smile = sum(smile_vals) / len(smile_vals) if smile_vals else 0.0
            frown = sum(frown_vals) / len(frown_vals) if frown_vals else 0.0
            # Output -1 (frown) to +1 (smile)
            return 0.5 + (smile - frown) * 0.5
        elif combine == "brow":
            # Brow: outer up + inner up - brow down
            up_vals = [blendshapes.get(n, 0.0) for n in names
                       if "Up" in n]
            down_vals = [blendshapes.get(n, 0.0) for n in names
                         if "Down" in n]
            up = sum(up_vals) / len(up_vals) if up_vals else 0.0
            down = sum(down_vals) / len(down_vals) if down_vals else 0.0
            return 0.5 + (up - down) * 0.5
        else:
            return sum(values) / len(values) if values else 0.0
