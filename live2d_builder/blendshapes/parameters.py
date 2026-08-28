#!/usr/bin/env python3
"""Standard Live2D Cubism 4 parameter definitions and BlendShape bindings.

Defines the full set of standard parameters (ParamAngleX/Y/Z, eye/mouth/brow
parameters, breathing, tears, etc.) plus custom extensions for hair and body
sway. Also provides 28 BlendShape definitions used by expressions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.logger import get_logger

log = get_logger("rigging.parameters")


# ------------------------------------------------------------------
# 28 standard parameters
# ------------------------------------------------------------------

STANDARD_PARAMETERS: List[Dict[str, Any]] = [
    # --- Head rotation ---
    {"id": "ParamAngleX",      "name": "Angle X",     "min": -30.0, "max": 30.0, "default": 0.0, "groups": ["head", "hair_front", "hair_back"]},
    {"id": "ParamAngleY",      "name": "Angle Y",     "min": -30.0, "max": 30.0, "default": 0.0, "groups": ["head", "hair_front"]},
    {"id": "ParamAngleZ",      "name": "Angle Z",     "min": -30.0, "max": 30.0, "default": 0.0, "groups": ["head", "hair_front", "hair_back"]},
    # --- Body rotation ---
    {"id": "ParamBodyAngleX",  "name": "Body Angle X","min": -10.0, "max": 10.0, "default": 0.0, "groups": ["body", "clothes"]},
    {"id": "ParamBodyAngleY",  "name": "Body Angle Y","min": -10.0, "max": 10.0, "default": 0.0, "groups": ["body", "clothes"]},
    {"id": "ParamBodyAngleZ",  "name": "Body Angle Z","min": -10.0, "max": 10.0, "default": 0.0, "groups": ["body", "clothes"]},
    # --- Eye open / blink ---
    {"id": "ParamEyeLOpen",    "name": "Eye L Open",  "min": 0.0,   "max": 1.0,  "default": 1.0, "groups": ["eyes"]},
    {"id": "ParamEyeROpen",    "name": "Eye R Open",  "min": 0.0,   "max": 1.0,  "default": 1.0, "groups": ["eyes"]},
    # --- Gaze ---
    {"id": "ParamEyeBallX",    "name": "Eyeball X",   "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["eyes"]},
    {"id": "ParamEyeBallY",    "name": "Eyeball Y",   "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["eyes"]},
    # --- Mouth ---
    {"id": "ParamMouthForm",   "name": "Mouth Form",  "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["mouth"]},
    {"id": "ParamMouthOpenY",  "name": "Mouth Open",  "min": 0.0,   "max": 1.0,  "default": 0.0, "groups": ["mouth"]},
    {"id": "ParamMouthSize",   "name": "Mouth Size",  "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["mouth"]},
    # --- Brows ---
    {"id": "ParamBrowLY",      "name": "Brow L Y",    "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["brows"]},
    {"id": "ParamBrowRY",      "name": "Brow R Y",    "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["brows"]},
    {"id": "ParamBrowLAngle",  "name": "Brow L Angle","min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["brows"]},
    {"id": "ParamBrowRAngle",  "name": "Brow R Angle","min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["brows"]},
    {"id": "ParamBrowLForm",   "name": "Brow L Form", "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["brows"]},
    {"id": "ParamBrowRForm",   "name": "Brow R Form", "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["brows"]},
    # --- Body / breathing ---
    {"id": "ParamBreath",      "name": "Breath",      "min": 0.0,   "max": 1.0,  "default": 0.5, "groups": ["body", "clothes"]},
    # --- Special ---
    {"id": "ParamCheek",       "name": "Cheek",       "min": 0.0,   "max": 1.0,  "default": 0.0, "groups": ["face"]},
    {"id": "ParamTears",       "name": "Tears",       "min": 0.0,   "max": 1.0,  "default": 0.0, "groups": ["eyes"]},
    {"id": "ParamTearsL",      "name": "Tears L",     "min": 0.0,   "max": 1.0,  "default": 0.0, "groups": ["eyes"]},
    {"id": "ParamTearsR",      "name": "Tears R",     "min": 0.0,   "max": 1.0,  "default": 0.0, "groups": ["eyes"]},
    {"id": "ParamHairSwing",   "name": "Hair Swing",  "min": -30.0, "max": 30.0, "default": 0.0, "groups": ["hair_front", "hair_back"]},
    {"id": "ParamBodySway",    "name": "Body Sway",   "min": -10.0, "max": 10.0, "default": 0.0, "groups": ["body", "clothes"]},
    {"id": "ParamShoulderL",   "name": "Shoulder L",  "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["body"]},
    {"id": "ParamShoulderR",   "name": "Shoulder R",  "min": -1.0,  "max": 1.0,  "default": 0.0, "groups": ["body"]},
]


# ------------------------------------------------------------------
# 28 BlendShape definitions (for expression system)
# ------------------------------------------------------------------

BLENDSHAPE_DEFINITIONS: List[Dict[str, Any]] = [
    {"name": "brow_neutral",   "parameter": "ParamBrowLForm",  "value": 0.0},
    {"name": "brow_raise_l",   "parameter": "ParamBrowLY",     "value": 1.0},
    {"name": "brow_raise_r",   "parameter": "ParamBrowRY",     "value": 1.0},
    {"name": "brow_furrow_l",  "parameter": "ParamBrowLY",     "value": -1.0},
    {"name": "brow_furrow_r",  "parameter": "ParamBrowRY",     "value": -1.0},
    {"name": "brow_angle_l",   "parameter": "ParamBrowLAngle", "value": 1.0},
    {"name": "brow_angle_r",   "parameter": "ParamBrowRAngle", "value": 1.0},
    {"name": "eye_open_l",     "parameter": "ParamEyeLOpen",   "value": 1.0},
    {"name": "eye_open_r",     "parameter": "ParamEyeROpen",   "value": 1.0},
    {"name": "eye_close_l",    "parameter": "ParamEyeLOpen",   "value": 0.0},
    {"name": "eye_close_r",    "parameter": "ParamEyeROpen",   "value": 0.0},
    {"name": "eye_blink",      "parameter": "ParamEyeLOpen",   "value": 0.0},
    {"name": "eye_gaze_left",  "parameter": "ParamEyeBallX",   "value": -1.0},
    {"name": "eye_gaze_right", "parameter": "ParamEyeBallX",   "value": 1.0},
    {"name": "eye_gaze_up",    "parameter": "ParamEyeBallY",   "value": -1.0},
    {"name": "eye_gaze_down",  "parameter": "ParamEyeBallY",   "value": 1.0},
    {"name": "mouth_open_a",   "parameter": "ParamMouthOpenY", "value": 1.0},
    {"name": "mouth_open_i",   "parameter": "ParamMouthForm",  "value": 1.0},
    {"name": "mouth_open_u",   "parameter": "ParamMouthForm",  "value": -0.5},
    {"name": "mouth_open_e",   "parameter": "ParamMouthForm",  "value": 0.5},
    {"name": "mouth_open_o",   "parameter": "ParamMouthOpenY", "value": 0.8},
    {"name": "mouth_smile",    "parameter": "ParamMouthForm",  "value": 1.0},
    {"name": "mouth_frown",    "parameter": "ParamMouthForm",  "value": -1.0},
    {"name": "cheek_blush",    "parameter": "ParamCheek",      "value": 1.0},
    {"name": "tears",          "parameter": "ParamTears",      "value": 1.0},
    {"name": "breath_normal",  "parameter": "ParamBreath",     "value": 0.5},
    {"name": "hair_swing",     "parameter": "ParamHairSwing",  "value": 10.0},
    {"name": "body_sway",      "parameter": "ParamBodySway",   "value": 5.0},
]


class ParameterSet:
    """Container and utility class for Live2D parameters.

    Behaves like a dict keyed by parameter ID, with helper methods for
    groups, defaults, BlendShape enumeration, and model3.json export.
    """

    def __init__(self, extra_parameters: Optional[List[Dict[str, Any]]] = None) -> None:
        self._params: Dict[str, Dict[str, Any]] = {}
        for p in STANDARD_PARAMETERS:
            self._params[p["id"]] = dict(p)
        if extra_parameters:
            for p in extra_parameters:
                self._params[p["id"]] = dict(p)
        self._blendshapes = {b["name"]: dict(b) for b in BLENDSHAPE_DEFINITIONS}

    # ------------------------------------------------------------------
    # Dict-like access
    # ------------------------------------------------------------------

    def __getitem__(self, key: str) -> Dict[str, Any]:
        return self._params[key]

    def __contains__(self, key: object) -> bool:
        return key in self._params

    def __iter__(self):
        return iter(self._params)

    def __len__(self) -> int:
        return len(self._params)

    def keys(self):
        return self._params.keys()

    def values(self):
        return self._params.values()

    def items(self):
        return self._params.items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._params.get(key, default)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def for_group(self, group: str) -> List[Dict[str, Any]]:
        """Return all parameters that affect a given group name."""
        if not isinstance(group, str):
            raise TypeError(f"group must be str, got {type(group).__name__}")
        return [p for p in self._params.values() if group in p.get("groups", [])]

    def get_default_values(self) -> Dict[str, float]:
        """Return ``{param_id: default_value}`` for all parameters."""
        return {pid: float(p["default"]) for pid, p in self._params.items()}

    def get_blendshapes(self) -> List[Dict[str, Any]]:
        """Return the 28 BlendShape definitions."""
        return [dict(b) for b in BLENDSHAPE_DEFINITIONS]

    def validate_parameter_values(self, values: Dict[str, float]) -> bool:
        """Check that all values are within their declared min/max ranges.

        Returns:
            True if all values are valid, False otherwise (logs warnings).
        """
        ok = True
        for pid, val in values.items():
            p = self._params.get(pid)
            if p is None:
                log.warning(f"Unknown parameter {pid} in values")
                ok = False
                continue
            if val < p["min"] or val > p["max"]:
                log.warning(f"Parameter {pid} value {val} out of range [{p['min']}, {p['max']}]")
                ok = False
        return ok

    def export_cubism_params(self) -> List[Dict[str, Any]]:
        """Export parameter definitions for model3.json ``Parameters`` array.

        Returns:
            List of ``{"Id": ..., "Value": default, "Min": ..., "Max": ...}`` dicts.
        """
        return [
            {
                "Id": p["id"],
                "Value": p["default"],
                "Min": p["min"],
                "Max": p["max"],
            }
            for p in self._params.values()
        ]

    def clamp(self, param_id: str, value: float) -> float:
        """Clamp a single value to its parameter's range."""
        p = self._params.get(param_id)
        if p is None:
            return value
        return max(p["min"], min(p["max"], float(value)))
