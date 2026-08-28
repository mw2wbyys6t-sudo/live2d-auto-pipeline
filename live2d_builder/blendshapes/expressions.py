#!/usr/bin/env python3
"""Expression generation for Live2D Cubism 4 models.

Produces ``exp3.json`` files (Cubism expression format) for 28 standard
facial expressions covering smile, angry, sad, surprised, crying, winks,
vowel mouth shapes, and more.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.logger import get_logger

log = get_logger("rigging.expressions")


# 28 standard expressions mapping name -> parameter overrides.
# Each entry is {param_id: value}. Values are clamped by ParameterSet.
STANDARD_EXPRESSIONS: Dict[str, Dict[str, float]] = {
    "neutral": {
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
        "ParamMouthForm": 0.0, "ParamMouthOpenY": 0.0,
        "ParamBrowLY": 0.0, "ParamBrowRY": 0.0,
        "ParamCheek": 0.0, "ParamTears": 0.0,
    },
    "smile": {
        "ParamMouthForm": 1.0, "ParamMouthOpenY": 0.2,
        "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9,
        "ParamCheek": 0.6,
    },
    "happy": {
        "ParamMouthForm": 1.0, "ParamMouthOpenY": 0.5,
        "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7,
        "ParamBrowLY": 0.3, "ParamBrowRY": 0.3,
        "ParamCheek": 0.8,
    },
    "angry": {
        "ParamBrowLY": -0.8, "ParamBrowRY": -0.8,
        "ParamBrowLAngle": -0.7, "ParamBrowRAngle": -0.7,
        "ParamMouthForm": -0.8, "ParamMouthOpenY": 0.1,
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
    },
    "sad": {
        "ParamBrowLY": -0.5, "ParamBrowRY": -0.5,
        "ParamBrowLAngle": 0.6, "ParamBrowRAngle": 0.6,
        "ParamMouthForm": -0.5, "ParamMouthOpenY": 0.1,
        "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7,
    },
    "surprised": {
        "ParamMouthOpenY": 0.8, "ParamMouthForm": 0.0,
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
        "ParamBrowLY": 0.8, "ParamBrowRY": 0.8,
    },
    "crying": {
        "ParamTears": 1.0,
        "ParamEyeLOpen": 0.3, "ParamEyeROpen": 0.3,
        "ParamBrowLY": -0.6, "ParamBrowRY": -0.6,
        "ParamBrowLAngle": 0.7, "ParamBrowRAngle": 0.7,
        "ParamMouthForm": -0.7, "ParamMouthOpenY": 0.3,
    },
    "shy": {
        "ParamCheek": 1.0,
        "ParamEyeLOpen": 0.6, "ParamEyeROpen": 0.6,
        "ParamBrowLY": 0.2, "ParamBrowRY": 0.2,
        "ParamMouthForm": 0.3, "ParamMouthOpenY": 0.0,
    },
    "sleepy": {
        "ParamEyeLOpen": 0.2, "ParamEyeROpen": 0.2,
        "ParamBrowLY": -0.3, "ParamBrowRY": -0.3,
        "ParamMouthOpenY": 0.1, "ParamMouthForm": -0.2,
    },
    "confused": {
        "ParamBrowLAngle": 0.5, "ParamBrowRAngle": -0.5,
        "ParamBrowLY": 0.3, "ParamBrowRY": -0.3,
        "ParamMouthForm": -0.3, "ParamMouthOpenY": 0.2,
        "ParamEyeLOpen": 0.8, "ParamEyeROpen": 0.9,
    },
    "determined": {
        "ParamBrowLY": -0.5, "ParamBrowRY": -0.5,
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
        "ParamMouthForm": -0.3, "ParamMouthOpenY": 0.0,
    },
    "embarrassed": {
        "ParamCheek": 1.0,
        "ParamEyeLOpen": 0.5, "ParamEyeROpen": 0.8,
        "ParamBrowLY": 0.4, "ParamBrowRY": 0.2,
        "ParamMouthForm": -0.2, "ParamMouthOpenY": 0.3,
    },
    "excited": {
        "ParamMouthOpenY": 0.9, "ParamMouthForm": 0.5,
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
        "ParamBrowLY": 0.6, "ParamBrowRY": 0.6,
    },
    "blink": {
        "ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0,
    },
    "wink_left": {
        "ParamEyeLOpen": 0.0, "ParamEyeROpen": 1.0,
        "ParamMouthForm": 0.5,
    },
    "wink_right": {
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 0.0,
        "ParamMouthForm": 0.5,
    },
    "wink_left_smile": {
        "ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.9,
        "ParamMouthForm": 1.0, "ParamCheek": 0.5,
    },
    "wink_right_smile": {
        "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.0,
        "ParamMouthForm": 1.0, "ParamCheek": 0.5,
    },
    "vowel_a": {
        "ParamMouthOpenY": 1.0, "ParamMouthForm": 0.0,
    },
    "vowel_i": {
        "ParamMouthOpenY": 0.2, "ParamMouthForm": 1.0,
    },
    "vowel_u": {
        "ParamMouthOpenY": 0.3, "ParamMouthForm": -0.5,
    },
    "vowel_e": {
        "ParamMouthOpenY": 0.5, "ParamMouthForm": 0.5,
    },
    "vowel_o": {
        "ParamMouthOpenY": 0.8, "ParamMouthForm": -0.8,
    },
    "serious": {
        "ParamBrowLY": -0.3, "ParamBrowRY": -0.3,
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
        "ParamMouthForm": -0.2,
    },
    "annoyed": {
        "ParamBrowLY": -0.5, "ParamBrowRY": -0.2,
        "ParamBrowLAngle": -0.4, "ParamBrowRAngle": -0.2,
        "ParamMouthForm": -0.5, "ParamEyeROpen": 0.7,
    },
    "scared": {
        "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
        "ParamBrowLY": 0.9, "ParamBrowRY": 0.9,
        "ParamMouthOpenY": 0.6, "ParamMouthForm": -0.3,
    },
    "love": {
        "ParamCheek": 1.0, "ParamMouthForm": 1.0,
        "ParamEyeLOpen": 0.5, "ParamEyeROpen": 0.5,
        "ParamBrowLY": 0.3, "ParamBrowRY": 0.3,
    },
    "smug": {
        "ParamMouthForm": 0.6, "ParamEyeLOpen": 0.6,
        "ParamEyeROpen": 0.8, "ParamBrowLAngle": -0.3,
        "ParamBrowRAngle": 0.2,
    },
}


class ExpressionBuilder:
    """Build and export Live2D Cubism expression files.

    Each expression maps to an ``exp3.json`` file containing a list of
    parameter targets with blend mode and value.
    """

    FADE_IN_TIME: float = 0.5
    FADE_OUT_TIME: float = 0.5

    def __init__(self, fade_in: float = 0.5, fade_out: float = 0.5) -> None:
        self.fade_in = fade_in
        self.fade_out = fade_out
        self._expressions: Dict[str, Dict[str, float]] = dict(STANDARD_EXPRESSIONS)

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def build_expression(
        self,
        name: str,
        overrides: Optional[Dict[str, float]] = None,
        fade_in: Optional[float] = None,
        fade_out: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Generate a single expression dict in exp3.json format.

        If ``name`` is one of the 28 standard expressions its base values
        are used and ``overrides`` are merged on top.  Custom names get
        only the override parameters.

        Args:
            name:     Expression name.
            overrides: Parameter values to set/override.
            fade_in:  Fade-in time in seconds (default: instance value).
            fade_out: Fade-out time in seconds.

        Returns:
            dict ready for JSON serialisation as ``<name>.exp3.json``.
        """
        base = dict(self._expressions.get(name, {}))
        if overrides:
            base.update(overrides)

        parameters: List[Dict[str, Any]] = []
        for pid, val in base.items():
            parameters.append({
                "Id": pid,
                "Value": float(val),
                "Blend": "Add",
            })

        return {
            "Type": "Live2D Expression",
            "FadeInTime": fade_in if fade_in is not None else self.fade_in,
            "FadeOutTime": fade_out if fade_out is not None else self.fade_out,
            "Parameters": parameters,
        }

    def build_all(self) -> List[Dict[str, Any]]:
        """Generate all 28 standard expressions.

        Returns:
            Sorted list of dicts with keys ``name``, ``file``, ``data``.
            ``file`` is the relative path used inside model3.json.
        """
        results: List[Dict[str, Any]] = []
        for name in sorted(self._expressions.keys()):
            data = self.build_expression(name)
            results.append({
                "name": name,
                "file": f"expressions/{name}.exp3.json",
                "data": data,
            })
        log.info(f"Built {len(results)} expressions")
        return results

    def add_expression(self, name: str, params: Dict[str, float]) -> None:
        """Register or replace a custom expression."""
        self._expressions[name] = dict(params)

    def get_expression_names(self) -> List[str]:
        """Return sorted list of available expression names."""
        return sorted(self._expressions.keys())

    def export_to_directory(self, output_dir: str) -> List[Dict[str, str]]:
        """Write all expression files to ``output_dir/expressions/``.

        Returns:
            List of {"Name": ..., "File": ...} entries for model3.json.
        """
        import json
        from pathlib import Path

        out = Path(output_dir)
        expr_dir = out / "expressions"
        expr_dir.mkdir(parents=True, exist_ok=True)

        manifest: List[Dict[str, str]] = []
        for entry in self.build_all():
            path = expr_dir / f"{entry['name']}.exp3.json"
            path.write_text(
                json.dumps(entry["data"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            manifest.append({"Name": entry["name"], "File": entry["file"]})

        log.info(f"Exported {len(manifest)} expressions to {expr_dir}")
        return manifest
