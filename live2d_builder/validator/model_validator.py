#!/usr/bin/env python3
"""Validation utilities for Live2D Cubism 4 model packages.

Validates:
- model3.json schema and references
- physics3.json structure
- Texture file existence and dimensions
- Expression file completeness
- Cubism Editor / VTube Studio / VSeeFace compatibility
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

log = get_logger("rigging.validator")


class ModelValidator:
    """Validate a Live2D model directory for correctness and compatibility."""

    REQUIRED_MODEL3_KEYS = ("Version", "FileReferences", "Groups")
    REQUIRED_FILE_REFS = ("Moc", "Textures")

    # Parameters that VTube Studio expects for face tracking
    VTS_TRACKING_PARAMS = [
        "ParamAngleX", "ParamAngleY", "ParamAngleZ",
        "ParamEyeLOpen", "ParamEyeROpen",
        "ParamEyeBallX", "ParamEyeBallY",
        "ParamMouthForm", "ParamMouthOpenY",
        "ParamBrowLY", "ParamBrowRY",
        "ParamBrowLAngle", "ParamBrowRAngle",
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_model3(self, model3_path: str) -> Tuple[bool, List[str]]:
        """Validate a model3.json file against the Cubism 4 schema.

        Returns:
            (ok, errors) tuple.
        """
        errors: List[str] = []
        path = Path(model3_path)

        if not path.exists():
            return False, [f"model3.json not found: {path}"]

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return False, [f"Invalid JSON: {exc}"]

        # Version
        version = data.get("Version")
        if version != 3:
            errors.append(f"Version must be 3, got {version!r}")

        # Required top-level keys
        for key in self.REQUIRED_MODEL3_KEYS:
            if key not in data:
                errors.append(f"Missing required top-level key: {key}")

        # FileReferences
        file_refs = data.get("FileReferences", {})
        for key in self.REQUIRED_FILE_REFS:
            if key not in file_refs:
                errors.append(f"FileReferences missing required key: {key}")

        textures = file_refs.get("Textures", [])
        if not isinstance(textures, list) or len(textures) == 0:
            errors.append("FileReferences.Textures must be a non-empty list")

        # Groups
        groups = data.get("Groups", [])
        if not isinstance(groups, list):
            errors.append("Groups must be a list")
        else:
            group_names = {g.get("Name") for g in groups if isinstance(g, dict)}
            if "EyeBlink" not in group_names:
                errors.append("Missing EyeBlink group")
            if "LipSync" not in group_names:
                errors.append("Missing LipSync group")

        # HitAreas (optional but warn if missing)
        hit_areas = data.get("HitAreas", [])
        if not hit_areas:
            log.debug("No HitAreas defined (optional)")

        # Parameters
        params = data.get("Parameters", [])
        if isinstance(params, list):
            param_ids = {p.get("Id") for p in params if isinstance(p, dict)}
            for required in ("ParamAngleX", "ParamEyeLOpen", "ParamMouthOpenY"):
                if required not in param_ids:
                    errors.append(f"Missing essential parameter: {required}")

        ok = len(errors) == 0
        if ok:
            log.info(f"model3.json validation passed: {path}")
        else:
            log.warning(f"model3.json validation found {len(errors)} error(s)")
        return ok, errors

    def validate_physics3(self, physics3_path: str) -> Tuple[bool, List[str]]:
        """Validate a physics3.json file structure."""
        errors: List[str] = []
        path = Path(physics3_path)

        if not path.exists():
            return False, [f"physics3.json not found: {path}"]

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return False, [f"Invalid JSON: {exc}"]

        if data.get("Version") != 3:
            errors.append(f"Version must be 3, got {data.get('Version')!r}")

        meta = data.get("Meta", {})
        if not isinstance(meta, dict):
            errors.append("Missing or invalid Meta section")
        else:
            if "PhysicsSettingCount" not in meta:
                errors.append("Meta.PhysicsSettingCount missing")

        settings = data.get("PhysicsSettings", [])
        if not isinstance(settings, list):
            errors.append("PhysicsSettings must be a list")
        else:
            for i, s in enumerate(settings):
                if "Id" not in s:
                    errors.append(f"PhysicsSettings[{i}] missing Id")
                if "Input" not in s or not s["Input"]:
                    errors.append(f"PhysicsSettings[{i}] ({s.get('Id', '?')}) has no Input")
                if "Output" not in s or not s["Output"]:
                    errors.append(f"PhysicsSettings[{i}] ({s.get('Id', '?')}) has no Output")
                if "Vertices" not in s:
                    errors.append(f"PhysicsSettings[{i}] missing Vertices")
                if "Normalization" not in s:
                    errors.append(f"PhysicsSettings[{i}] missing Normalization")

        ok = len(errors) == 0
        if ok:
            log.info(f"physics3.json validation passed: {path}")
        return ok, errors

    def validate_textures(
        self,
        model3: Dict[str, Any],
        base_dir: str,
    ) -> Tuple[bool, List[str]]:
        """Validate that all referenced textures exist and are valid images."""
        errors: List[str] = []
        base = Path(base_dir)
        textures = model3.get("FileReferences", {}).get("Textures", [])

        if not textures:
            return False, ["No textures referenced in model3.json"]

        for tex_rel in textures:
            tex_path = base / tex_rel
            if not tex_path.exists():
                errors.append(f"Texture not found: {tex_rel}")
                continue
            try:
                from PIL import Image
                with Image.open(tex_path) as img:
                    w, h = img.size
                    if w < 64 or h < 64:
                        errors.append(f"Texture {tex_rel} is too small ({w}x{h})")
                    if img.mode not in ("RGBA", "RGB"):
                        errors.append(f"Texture {tex_rel} has unsupported mode: {img.mode}")
            except Exception as exc:
                errors.append(f"Cannot read texture {tex_rel}: {exc}")

        ok = len(errors) == 0
        return ok, errors

    def validate_expressions(self, expr_dir: str) -> Tuple[bool, List[str]]:
        """Validate all .exp3.json files in a directory."""
        errors: List[str] = []
        d = Path(expr_dir)

        if not d.exists():
            return False, [f"Expressions directory not found: {d}"]

        expr_files = sorted(d.glob("*.exp3.json"))
        if not expr_files:
            log.warning(f"No expression files found in {d}")
            return True, []  # Expressions are optional

        for fpath in expr_files:
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                errors.append(f"{fpath.name}: invalid JSON ({exc})")
                continue

            if data.get("Type") != "Live2D Expression":
                errors.append(f"{fpath.name}: Type must be 'Live2D Expression'")
            params = data.get("Parameters", [])
            if not isinstance(params, list) or len(params) == 0:
                errors.append(f"{fpath.name}: empty Parameters list")
            else:
                for p in params:
                    if "Id" not in p or "Value" not in p:
                        errors.append(f"{fpath.name}: parameter missing Id or Value")

        ok = len(errors) == 0
        if ok:
            log.info(f"Validated {len(expr_files)} expression file(s) in {d}")
        return ok, errors

    # ------------------------------------------------------------------
    # Full validation report
    # ------------------------------------------------------------------

    def validate_all(self, model_dir: str) -> Dict[str, Any]:
        """Run all validators and return a comprehensive report.

        Args:
            model_dir: Directory containing model3.json and companion files.

        Returns:
            dict with ``valid`` (bool), ``checks`` (per-check results),
            ``errors`` (all errors), ``warnings``.
        """
        d = Path(model_dir)
        errors: List[str] = []
        warnings: List[str] = []
        checks: Dict[str, Any] = {}

        # Locate model3.json
        model3_candidates = sorted(d.glob("*.model3.json"))
        if not model3_candidates:
            return {
                "valid": False,
                "checks": {},
                "errors": ["No *.model3.json found in directory"],
                "warnings": [],
            }

        model3_path = model3_candidates[0]
        base_dir = model3_path.parent

        # Load model3 once
        try:
            model3_data = json.loads(model3_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "valid": False,
                "checks": {},
                "errors": [f"Cannot parse model3.json: {exc}"],
                "warnings": [],
            }

        # 1. model3 schema
        ok, errs = self.validate_model3(str(model3_path))
        checks["model3"] = {"valid": ok, "errors": errs}
        errors.extend(errs)

        # 2. Textures
        ok, errs = self.validate_textures(model3_data, str(base_dir))
        checks["textures"] = {"valid": ok, "errors": errs}
        errors.extend(errs)

        # 3. Physics
        physics_ref = model3_data.get("FileReferences", {}).get("Physics", "")
        if physics_ref:
            physics_path = base_dir / physics_ref
            if physics_path.exists():
                ok, errs = self.validate_physics3(str(physics_path))
                checks["physics3"] = {"valid": ok, "errors": errs}
                errors.extend(errs)
            else:
                warnings.append(f"Physics file referenced but not found: {physics_ref}")
        else:
            warnings.append("No physics file referenced")

        # 4. Expressions
        expr_refs = model3_data.get("FileReferences", {}).get("Expressions", [])
        expr_dir = base_dir / "expressions"
        if expr_dir.exists():
            ok, errs = self.validate_expressions(str(expr_dir))
            checks["expressions"] = {"valid": ok, "errors": errs, "count": len(expr_refs)}
            errors.extend(errs)
        elif expr_refs:
            warnings.append("Expressions referenced but directory not found")

        # 5. moc3 existence (Cubism Editor output — will be missing pre-build)
        moc_ref = model3_data.get("FileReferences", {}).get("Moc", "")
        if moc_ref:
            moc_path = base_dir / moc_ref
            if not moc_path.exists():
                warnings.append(
                    f"moc3 file not found ({moc_ref}). Generate via Cubism Editor export."
                )
            else:
                checks["moc3"] = {"valid": True, "path": str(moc_path)}

        valid = len(errors) == 0
        report = {
            "valid": valid,
            "model_dir": str(d),
            "model3_json": str(model3_path),
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
        }

        if valid:
            log.success(f"Model validation passed for {model_dir}")
        else:
            log.warning(f"Model validation found {len(errors)} error(s)")
        return report

    # ------------------------------------------------------------------
    # Compatibility check
    # ------------------------------------------------------------------

    def check_cubism_compatibility(self, model_dir: str) -> Dict[str, Any]:
        """Check compatibility with VTube Studio, VSeeFace, and Cubism Editor.

        Returns:
            dict with per-application compatibility status and notes.
        """
        d = Path(model_dir)
        model3_candidates = sorted(d.glob("*.model3.json"))
        if not model3_candidates:
            return {
                "cubism_editor": {"compatible": False, "notes": ["No model3.json found"]},
                "vtube_studio": {"compatible": False, "notes": ["No model3.json found"]},
                "vseeface": {"compatible": False, "notes": ["No model3.json found"]},
            }

        model3_path = model3_candidates[0]
        try:
            model3_data = json.loads(model3_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "cubism_editor": {"compatible": False, "notes": [f"Parse error: {exc}"]},
                "vtube_studio": {"compatible": False, "notes": [f"Parse error: {exc}"]},
                "vseeface": {"compatible": False, "notes": [f"Parse error: {exc}"]},
            }

        param_ids = {
            p.get("Id") for p in model3_data.get("Parameters", [])
            if isinstance(p, dict)
        }
        moc_ref = model3_data.get("FileReferences", {}).get("Moc", "")
        moc_exists = (d / moc_ref).exists() if moc_ref else False
        textures = model3_data.get("FileReferences", {}).get("Textures", [])

        results: Dict[str, Any] = {}

        # Cubism Editor
        cubism_notes: List[str] = []
        if model3_data.get("Version") == 3:
            cubism_notes.append("model3.json Version 3 — compatible with Cubism 4.x")
        else:
            cubism_notes.append("Version mismatch; Cubism 4 expects Version 3")
        if not moc_exists:
            cubism_notes.append("moc3 missing — import model3.json into Cubism Editor and export moc3")
        results["cubism_editor"] = {
            "compatible": model3_data.get("Version") == 3,
            "notes": cubism_notes,
        }

        # VTube Studio
        vts_notes: List[str] = []
        missing_vts = [p for p in self.VTS_TRACKING_PARAMS if p not in param_ids]
        if missing_vts:
            vts_notes.append(f"Missing tracking parameters: {', '.join(missing_vts)}")
        if not moc_exists:
            vts_notes.append("moc3 missing — VTube Studio requires moc3 binary")
        if not textures:
            vts_notes.append("No textures referenced")
        vts_ok = len(missing_vts) == 0 and moc_exists and bool(textures)
        results["vtube_studio"] = {"compatible": vts_ok, "notes": vts_notes}

        # VSeeFace
        vseeface_notes: List[str] = []
        # VSeeFace needs similar params but tolerates missing some
        essential = ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen",
                     "ParamMouthOpenY", "ParamMouthForm"]
        missing_vsf = [p for p in essential if p not in param_ids]
        if missing_vsf:
            vseeface_notes.append(f"Missing essential params: {', '.join(missing_vsf)}")
        if not moc_exists:
            vseeface_notes.append("moc3 missing — VSeeFace requires moc3")
        vsf_ok = len(missing_vsf) == 0 and moc_exists
        results["vseeface"] = {"compatible": vsf_ok, "notes": vseeface_notes}

        ce = results["cubism_editor"]["compatible"]
        vt = results["vtube_studio"]["compatible"]
        vs = results["vseeface"]["compatible"]
        log.info(f"Compatibility: Cubism={ce}, VTS={vt}, VSeeFace={vs}")
        return results
