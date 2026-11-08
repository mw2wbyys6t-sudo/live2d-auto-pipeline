from __future__ import annotations

import json
import math
import argparse
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from PIL import Image


LEVELS = ("L0", "L1", "L2", "L3")
STATUSES = {"PASS", "FAIL", "BLOCKED", "INFO"}


def check(check_id: str, status: str, measured: Any = None,
          threshold: Any = None, evidence: Any = None,
          blocker: Optional[str] = None) -> Dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"invalid check status: {status}")
    result = {"id": check_id, "status": status, "measured": measured,
              "threshold": threshold, "evidence": evidence}
    if blocker:
        result["blocker"] = blocker
    return result


def run_levels(level_results: Dict[str, List[Dict[str, Any]]],
               levels: Iterable[str] = LEVELS) -> Dict[str, Any]:
    selected = list(levels)
    results: Dict[str, Dict[str, Any]] = {}
    blocked = False
    overall = "VALIDATED"
    for level in selected:
        checks = list(level_results.get(level, []))
        if blocked:
            checks = [check(f"{level.lower()}_blocked", "BLOCKED",
                            blocker="A preceding level did not pass")]
            status = "BLOCKED"
        elif not checks:
            checks = [check(f"{level.lower()}_evidence", "BLOCKED",
                            blocker="No required checks were produced")]
            status = "BLOCKED"
            blocked = True
        else:
            statuses = {item["status"] for item in checks}
            status = ("FAIL" if "FAIL" in statuses else
                      "BLOCKED" if "BLOCKED" in statuses else
                      "PASS" if statuses <= {"PASS", "INFO"} and "PASS" in statuses
                      else "BLOCKED")
            if status in {"FAIL", "BLOCKED"}:
                blocked = True
                overall = "FAIL" if status == "FAIL" else "BLOCKED"
        results[level] = {"id": level, "status": status, "checks": checks}
    if any(item["status"] == "FAIL" for item in results.values()):
        overall = "FAIL"
    elif any(item["status"] == "BLOCKED" for item in results.values()):
        overall = "BLOCKED"
    return {"state": overall, "levels": results}


def build_report(manifest: str, level_results: Dict[str, List[Dict[str, Any]]],
                 levels: Iterable[str] = LEVELS) -> Dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    result = run_levels(level_results, levels)
    result.update({"schema_version": 1, "manifest": str(manifest),
                   "started_at": started,
                   "finished_at": datetime.now(timezone.utc).isoformat()})
    return result


def _normalize_parameter(parameter: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(parameter, dict):
        return None
    normalized = {"Id": parameter.get("Id")}
    for canonical, cubism in (("Minimum", "Min"), ("Default", "Value"),
                              ("Maximum", "Max")):
        has_canonical = canonical in parameter
        has_cubism = cubism in parameter
        if has_canonical and has_cubism and parameter[canonical] != parameter[cubism]:
            return None
        if has_canonical:
            normalized[canonical] = parameter[canonical]
        elif has_cubism:
            normalized[canonical] = parameter[cubism]
        else:
            return None
    return normalized


def validate_manifest(manifest_path: Path) -> List[Dict[str, Any]]:
    path = Path(manifest_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [check("manifest_json", "FAIL", evidence={"path": str(path)},
                      blocker=str(exc))]
    if not isinstance(data, dict):
        return [check("manifest_json", "FAIL", evidence={"path": str(path)},
                      blocker="Manifest root must be an object")]
    refs = data.get("FileReferences")
    parameters = data.get("Parameters")
    schema_valid = (data.get("Version") == 3 and isinstance(refs, dict) and isinstance(parameters, list) and
                    isinstance(refs.get("Moc"), str) and bool(refs.get("Moc")) and
                    isinstance(refs.get("Textures"), list) and
                    all(isinstance(item, str) and bool(item)
                        for item in refs.get("Textures", [])))
    refs = refs if isinstance(refs, dict) else {}
    model_root = path.parent.resolve()
    resolved = []
    contained = True
    def add_reference(key: str, item: Any) -> None:
        nonlocal contained
        if not isinstance(item, str) or not item:
            return
        target = (path.parent / item).resolve()
        try:
            target.relative_to(model_root)
        except ValueError:
            contained = False
        resolved.append((key, item, target))

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif key in {"Moc", "Textures", "Physics", "Pose", "UserData", "DisplayInfo", "File", "FileName", "Path"}:
            add_reference(key, value)

    visit(data.get("FileReferences", {}))
    visit(data.get("Motions", {}))
    visit(data.get("Expressions", []))
    checks = [check("manifest_json", "PASS", evidence={"path": str(path)}),
              check("manifest_schema", "PASS" if schema_valid else "FAIL",
                    evidence={"file_references_type": type(data.get("FileReferences")).__name__,
                              "parameters_type": type(data.get("Parameters")).__name__}),
              check("texture_references", "PASS" if schema_valid and
                    bool(refs.get("Textures")) and all(
                        item[0] == "Textures" and item[2].is_file() and
                        _readable_png(item[2])
                        for item in resolved if item[0] == "Textures") else "FAIL",
                    evidence={"textures": refs.get("Textures")}),
              check("reference_path_contained", "PASS" if contained else "FAIL",
                    evidence={"references": [item[1] for item in resolved]}),
              check("referenced_files_exist",
                    "PASS" if resolved and all(item[2].is_file() for item in resolved)
                    else "FAIL",
                    evidence={"missing": [item[1] for item in resolved
                                           if not item[2].is_file()]})]
    for key in ("Parameters",):
        params = parameters if isinstance(parameters, list) else []
        ids = [item.get("Id") for item in params if isinstance(item, dict)]
        unique = (all(isinstance(value, str) and value for value in ids) and
                  len(ids) == len(set(ids)) and len(ids) == len(params))
        ranges_valid = True
        for item in params:
            normalized = _normalize_parameter(item)
            if normalized is None or not all(
                    name in normalized for name in ("Minimum", "Default", "Maximum")):
                ranges_valid = False
                continue
            values = (normalized["Minimum"], normalized["Default"], normalized["Maximum"])
            try:
                ranges_valid = (ranges_valid and
                                all(isinstance(value, (int, float)) and
                                    not isinstance(value, bool) and math.isfinite(value)
                                    for value in values) and
                                values[0] <= values[1] <= values[2])
            except (TypeError, ValueError):
                ranges_valid = False
        ranges_complete = all(
            (normalized := _normalize_parameter(item)) is not None and
            all(name in normalized for name in ("Id", "Minimum", "Default", "Maximum"))
            for item in params)
        checks.extend([
            check("parameter_ids_unique", "PASS" if unique else "FAIL",
                  evidence={"ids": ids}),
            check("parameter_ranges", "PASS" if ranges_valid and ranges_complete
                  else "FAIL", evidence={"ids": ids}),
        ])
    group_ids = []
    groups = data.get("Groups")
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("Ids"), list):
                group_ids.extend(group["Ids"])
    checks.append(check("group_ids_unique", "PASS" if
                        all(isinstance(value, str) and value for value in group_ids) and
                        len(group_ids) == len(set(group_ids)) else "FAIL",
                        evidence={"ids": group_ids}))
    return checks


def _readable_png(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def validate_meshes(meshes: List[Dict[str, Any]], texture_page_count: Optional[int] = None,
                    epsilon: float = 1e-10) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    if not isinstance(meshes, (list, tuple)) or not meshes:
        return [check("mesh_evidence", "FAIL", evidence={"mesh_count": 0},
                      blocker="Mesh collection must be a non-empty list")]
    for mesh_idx, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            checks.append(check("mesh_evidence", "FAIL",
                                evidence={"mesh_index": mesh_idx},
                                blocker="Mesh entry must be an object"))
            continue
        mesh_id = mesh.get("id", "unknown")
        raw_vertices = mesh.get("vertices")
        vertices = raw_vertices if isinstance(raw_vertices, (list, tuple)) else []
        valid_vertices = isinstance(raw_vertices, (list, tuple)) and bool(vertices)
        normalized_vertices = []
        for vertex in vertices:
            try:
                point = [float(value) for value in vertex]
                if len(point) != 2 or not all(math.isfinite(value) for value in point):
                    valid_vertices = False
                normalized_vertices.append(point if len(point) == 2 else [0, 0])
            except (TypeError, ValueError, OverflowError):
                valid_vertices = False
                normalized_vertices.append([0, 0])
        raw_indices = mesh.get("indices")
        indices = raw_indices if isinstance(raw_indices, (list, tuple)) else []
        valid_indices = valid_vertices and bool(indices)
        degenerate = [] if indices else [0]
        for tri_idx, tri in enumerate(indices):
            if (not isinstance(tri, (list, tuple)) or len(tri) != 3 or
                    any(not isinstance(i, int) or isinstance(i, bool) or
                        i < 0 or i >= len(vertices) for i in tri)):
                valid_indices = False
                degenerate.append(tri_idx)
                continue
            if len(set(tri)) < 3:
                degenerate.append(tri_idx)
                continue
            a, b, c = (normalized_vertices[i] for i in tri)
            area2 = abs((b[0] - a[0]) * (c[1] - a[1]) -
                        (b[1] - a[1]) * (c[0] - a[0]))
            if not valid_vertices or not math.isfinite(area2) or area2 <= epsilon:
                degenerate.append(tri_idx)
        uvs = mesh.get("uvs")
        if uvs is None:
            uv_status = "BLOCKED"
            uv_blocker = "UV evidence is missing"
        else:
            try:
                finite_uvs = (isinstance(uvs, (list, tuple)) and len(uvs) == len(vertices) and
                              all(isinstance(uv, (list, tuple)) and len(uv) == 2 and
                                  all(math.isfinite(float(value)) and
                                      0 <= float(value) <= 1 for value in uv)
                                  for uv in uvs))
            except (TypeError, ValueError, OverflowError):
                finite_uvs = False
            uv_status = "PASS" if finite_uvs else "FAIL"
            uv_blocker = None
        page = mesh.get("texture_page")
        valid_page_count = (texture_page_count is None or
                            isinstance(texture_page_count, int) and
                            not isinstance(texture_page_count, bool) and
                            texture_page_count > 0)
        valid_page = (isinstance(page, int) and not isinstance(page, bool) and page >= 0 and
                      valid_page_count and
                      (texture_page_count is None or page < texture_page_count))
        page_status = ("BLOCKED" if "texture_page" not in mesh else
                       "PASS" if valid_page else "FAIL")
        checks.extend([
            check("mesh_indices", "PASS" if valid_indices else "FAIL",
                  evidence={"mesh_id": mesh_id}),
            check("mesh_degenerate_triangles", "FAIL" if degenerate else "PASS",
                  evidence={"mesh_id": mesh_id, "triangle_indices": degenerate}),
            check("mesh_uv_finite", uv_status, blocker=uv_blocker,
                  evidence={"mesh_id": mesh_id}),
            check("mesh_texture_page", page_status,
                  evidence={"mesh_id": mesh_id, "page": page,
                            "texture_page_count": texture_page_count}),
        ])
        if not isinstance(raw_vertices, (list, tuple)):
            checks.append(check("mesh_evidence", "FAIL",
                                evidence={"mesh_id": mesh_id},
                                blocker="Mesh vertices must be a list"))
    return checks


def load_mesh_evidence(mesh_path: Path, texture_page_count: Optional[int] = None) -> List[Dict[str, Any]]:
    path = Path(mesh_path)
    if not path.is_file():
        return [check("mesh_evidence", "BLOCKED", evidence={"path": str(path)},
                      blocker="mesh evidence file is missing")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [check("mesh_evidence", "FAIL", evidence={"path": str(path)},
                      blocker=str(exc))]
    if not isinstance(data, dict) or not data:
        return [check("mesh_evidence", "BLOCKED", evidence={"path": str(path)},
                      blocker="mesh evidence contains no meshes")]
    meshes = []
    malformed = False
    for mesh_id, mesh in data.items():
        if not isinstance(mesh, dict):
            malformed = True
            continue
        meshes.append({"id": mesh_id, **mesh})
    if malformed or not meshes:
        return [check("mesh_evidence", "FAIL", evidence={"path": str(path)},
                      blocker="mesh evidence entries must be objects")]
    texture_count = texture_page_count
    return validate_meshes(meshes, texture_count)


def _core_status(result: Dict[str, Any]) -> str:
    if result.get("ok"):
        return "PASS"
    if (result.get("timed_out") or result.get("crashed") or
            result.get("environment_blocked") or result.get("exit_code") == 2):
        return "BLOCKED"
    return "FAIL"


def run_l1(manifest_path: Path, evidence_dir: Path) -> List[Dict[str, Any]]:
    from drivers.live2d_runtime import moc3_verify

    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = Path(manifest_path)
    moc_path = path.parent / (json.loads(path.read_text(encoding="utf-8"))
                              .get("FileReferences", {}).get("Moc", ""))
    consistency = moc3_verify.verify_moc3_consistency(str(moc_path))
    loaded = moc3_verify.verify_moc3_load(str(path))
    image_path = evidence_dir / "default.png"
    rendered = moc3_verify.render_probe(str(path), png=str(image_path), prime=True)
    probe_logs = {
        "core_consistency": {"stdout": consistency.get("stdout", ""),
                             "stderr": consistency.get("stderr", "")},
        "core_load": {"stdout": loaded.get("stdout", ""),
                      "stderr": loaded.get("stderr", "")},
        "default_render": {"stdout": rendered.get("stdout", ""),
                           "stderr": rendered.get("stderr", "")},
    }
    (evidence_dir / "probe_logs.json").write_text(
        json.dumps(probe_logs, ensure_ascii=False, indent=2), encoding="utf-8")
    render_status = _core_status(rendered)
    if render_status == "PASS":
        try:
            from PIL import Image
            with Image.open(image_path) as image:
                if image.mode not in {"RGBA", "LA"} and "transparency" not in image.info:
                    has_visible_pixels = True
                else:
                    alpha = image.convert("RGBA").getchannel("A")
                    has_visible_pixels = alpha.getbbox() is not None
            if not has_visible_pixels:
                render_status = "FAIL"
        except (OSError, ValueError):
            render_status = "FAIL"
    checks = [
        check("core_consistency", _core_status(consistency),
              evidence={"moc": str(moc_path)}, blocker=consistency.get("blocker")),
        check("core_load", _core_status(loaded),
              evidence={"parameter_ids": loaded.get("parameter_ids", [])},
              blocker=loaded.get("blocker")),
        check("default_render", render_status,
              measured={"opaque_pixels": rendered.get("opaque_pixels"),
                        "alpha_bbox": rendered.get("alpha_bbox")},
              evidence={"png": str(image_path)}, blocker=rendered.get("blocker")),
    ]
    return checks


def _parameter_family(parameter_id: str) -> str:
    match = re.match(r"(Param[A-Za-z]+)", parameter_id)
    return match.group(1) if match else parameter_id


def _read_png(path: Path):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(path).convert("RGBA"))


def _bbox(mask):
    import numpy as np

    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _l3_threshold(threshold):
    if isinstance(threshold, dict):
        threshold = threshold.get("max_changed_bbox_area")
    if (not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or
            not math.isfinite(threshold) or threshold < 0):
        return None
    return threshold


def run_l3(manifest_path: Path, evidence_dir: Path,
           thresholds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    from drivers.live2d_runtime import moc3_verify
    import numpy as np

    path = Path(manifest_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    params = data.get("Parameters") or []
    normalized_params = [_normalize_parameter(parameter) for parameter in params]
    if any(parameter is None for parameter in normalized_params):
        return [check("parameter_ranges", "FAIL",
                      evidence={"parameter_ids": [item.get("Id") for item in params
                                                  if isinstance(item, dict)]},
                      blocker="Parameter range fields are missing or conflicting")]
    params = normalized_params
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    render_dir = Path(tempfile.mkdtemp(prefix="l3-", dir=evidence_dir))

    def evidence_path(image_path):
        return image_path.relative_to(evidence_dir).as_posix()

    baseline = None
    baseline_result = {}
    baseline_path = None
    checks: List[Dict[str, Any]] = []
    used_image_paths = set()
    for parameter in params:
        parameter_id = parameter["Id"]
        bounds = [("min", parameter["Minimum"]),
                  ("default", parameter["Default"]),
                  ("max", parameter["Maximum"])]
        if baseline is None:
            bounds = [("default", parameter["Default"]),
                      ("min", parameter["Minimum"]),
                      ("max", parameter["Maximum"])]
        renders = []
        failures = []
        for label, value in bounds:
            safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", parameter_id)
            image_name = f"{safe_id}_{label}.png"
            image_path = render_dir / image_name
            suffix = 2
            while image_path in used_image_paths:
                image_path = render_dir / f"{safe_id}_{suffix}_{label}.png"
                suffix += 1
            used_image_paths.add(image_path)
            fixed_params = {item["Id"]: float(item["Default"]) for item in params}
            fixed_params[parameter_id] = float(value)
            result = moc3_verify.render_probe(
                str(path), params=fixed_params,
                png=str(image_path), prime=False)
            applied = result.get("parameters_applied")
            if (not result.get("ok") or applied is False or
                    isinstance(applied, dict) and any(value is False for value in applied.values())):
                failures.append(result)
                continue
            image = _read_png(image_path) if image_path.is_file() else None
            renders.append((label, value, result, image, image_path))
            if label == "default" and baseline is None and image is not None:
                baseline = image
                baseline_result = result
                baseline_path = image_path
        if failures:
            blocked = any(_core_status(item) == "BLOCKED" for item in failures)
            checks.append(check(
                f"parameter_scan:{parameter_id}", "BLOCKED" if blocked else "FAIL",
                evidence={"parameter_id": parameter_id,
                          "parameters_applied": [item.get("parameters_applied")
                                                 for item in failures],
                          "blockers": [item.get("blocker") for item in failures]},
                blocker="Core parameter render failed"))
            continue
        endpoint_evidence = {}
        areas = []
        for label, value, result, image, image_path in renders:
            if label == "default":
                default_result = result
                continue
            if baseline is None or image is None or baseline.shape != image.shape:
                endpoint_evidence[label] = {
                    "value": value, "png": evidence_path(image_path),
                    "alpha_bbox": result.get("alpha_bbox"),
                    "centroid": result.get("centroid"),
                    "opaque_pixels": result.get("opaque_pixels"),
                    "changed_pixels": None, "changed_bbox": None}
                continue
            changed_mask = np.any(baseline != image, axis=2)
            area = int(changed_mask.sum())
            changed_bbox = _bbox(changed_mask)
            bbox_area = (0 if changed_bbox is None else
                         (changed_bbox[2] - changed_bbox[0] + 1) *
                         (changed_bbox[3] - changed_bbox[1] + 1))
            areas.append(bbox_area)
            endpoint_evidence[label] = {
                "value": value, "png": evidence_path(image_path),
                "alpha_bbox": result.get("alpha_bbox"),
                "centroid": result.get("centroid"),
                "opaque_pixels": result.get("opaque_pixels"),
                "changed_pixels": area, "changed_bbox": changed_bbox,
                "changed_bbox_area": bbox_area}
        if baseline is None:
            response_status = "BLOCKED"
            blocker = "Fresh default render snapshot is unavailable"
        elif any(value["changed_pixels"] is None for value in endpoint_evidence.values()):
            response_status = "BLOCKED"
            blocker = "Parameter render image is unavailable or incompatible with fresh default snapshot"
        elif all(value["changed_pixels"] == 0 for value in endpoint_evidence.values()):
            response_status = "FAIL"
            blocker = "Parameter extremes produced no visible change"
        elif all(value["changed_pixels"] == 0 or value["changed_bbox_area"] == 0
                 for value in endpoint_evidence.values()):
            response_status = "BLOCKED"
            blocker = "Parameter response evidence is inconsistent"
        else:
            response_status = "PASS"
            blocker = None
        checks.append(check(
            f"parameter_response:{parameter_id}", response_status,
            measured={"min_changed_pixels": endpoint_evidence.get("min", {}).get("changed_pixels"),
                      "max_changed_pixels": endpoint_evidence.get("max", {}).get("changed_pixels")},
            evidence={"parameter_id": parameter_id,
                      "baseline_png": evidence_path(baseline_path),
                      "baseline_alpha_bbox": baseline_result.get("alpha_bbox"),
                      "baseline_centroid": baseline_result.get("centroid"),
                      "default_png": evidence_path(next(
                          image_path for label, _, _, _, image_path in renders
                          if label == "default")),
                      "default_alpha_bbox": default_result.get("alpha_bbox"),
                      "parameters_applied": default_result.get("parameters_applied"),
                      "endpoints": endpoint_evidence}, blocker=blocker))
        family = _parameter_family(parameter_id)
        threshold = (thresholds or {}).get(family, (thresholds or {}).get(parameter_id))
        max_bbox_area = _l3_threshold(threshold)
        if max_bbox_area is None:
            locality_status = "BLOCKED"
        elif any(value["changed_pixels"] is None for value in endpoint_evidence.values()):
            locality_status = "BLOCKED"
        else:
            locality_status = ("PASS" if all(area <= max_bbox_area for area in areas)
                               else "FAIL")
        checks.append(check(
            f"parameter_locality:{parameter_id}", locality_status,
            measured={"changed_bbox_areas": areas,
                      "max_changed_bbox_area": max(areas) if areas else None},
            threshold=threshold,
            evidence={"parameter_id": parameter_id, "family": family,
                      "endpoints": endpoint_evidence},
            blocker="No explicit max_changed_bbox_area threshold configured"
            if max_bbox_area is None else None))
    if not checks:
        return [check("parameter_scan", "BLOCKED", blocker="No declared parameters")]
    return checks


def run_l2(manifest_path: Path) -> List[Dict[str, Any]]:
    path = Path(manifest_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [check("l2_manifest", "FAIL", evidence={"path": str(path)},
                      blocker=str(exc))]
    if not isinstance(data, dict):
        return [check("l2_manifest", "FAIL", evidence={"path": str(path)},
                      blocker="Manifest root must be an object")]
    refs = data.get("FileReferences")
    if not isinstance(refs, dict) or not isinstance(refs.get("Moc"), str) or not refs["Moc"]:
        return [check("l2_manifest", "FAIL", evidence={"path": str(path)},
                      blocker="Manifest must include FileReferences.Moc")]
    textures = refs.get("Textures")
    if not isinstance(textures, list) or not textures or not all(
            isinstance(item, str) and item for item in textures):
        return [check("l2_manifest", "FAIL", evidence={"path": str(path)},
                      blocker="Manifest must include a non-empty Textures list")]
    moc_ref = refs.get("Moc", "")
    mesh_path = path.parent / f"{Path(moc_ref).stem}.meshes.json"
    return load_mesh_evidence(mesh_path, len(textures))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--pose", action="append", default=[])
    parser.add_argument("--baseline-pose-png", type=Path)
    parser.add_argument("--baseline-rest-png", type=Path)
    parser.add_argument("--baseline-rest-sha")
    parser.add_argument("--baseline-rest-opaque", type=int)
    parser.add_argument("--max-rest-changed", type=int, default=64)
    parser.add_argument("--max-new-transparent", type=int, default=200)
    parser.add_argument("--width", type=int, default=400)
    parser.add_argument("--height", type=int, default=500)
    parser.add_argument("--levels")
    parser.add_argument("--parameter-thresholds", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("rig_acceptance_report.json"))
    args = parser.parse_args(argv)
    if args.levels is None and (args.pose or args.baseline_pose_png or
                            args.baseline_rest_png or args.baseline_rest_sha or
                            args.baseline_rest_opaque is not None):
        from rig_visual_gate import main as visual_main
        visual_args = ["--manifest", args.manifest, "--out", str(args.out)]
        for pose in args.pose:
            visual_args.extend(["--pose", pose])
        if args.baseline_pose_png:
            visual_args.extend(["--baseline-pose-png", str(args.baseline_pose_png)])
        if args.baseline_rest_png:
            visual_args.extend(["--baseline-rest-png", str(args.baseline_rest_png)])
        if args.baseline_rest_sha:
            visual_args.extend(["--baseline-rest-sha", args.baseline_rest_sha])
        if args.baseline_rest_opaque is not None:
            visual_args.extend(["--baseline-rest-opaque",
                                str(args.baseline_rest_opaque)])
        visual_args.extend(["--max-rest-changed", str(args.max_rest_changed),
                            "--max-new-transparent", str(args.max_new_transparent),
                            "--width", str(args.width), "--height", str(args.height)])
        return visual_main(visual_args)
    manifest = Path(args.manifest)
    out = Path(args.out)
    evidence_dir = args.evidence_dir or out.parent / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    requested = [level.strip() for level in (args.levels or "L0,L1,L2,L3").split(",") if level.strip()]
    selected = [level for level in LEVELS if level in requested]
    selected.extend(level for level in requested if level not in LEVELS)
    started = datetime.now(timezone.utc)
    level_results: Dict[str, List[Dict[str, Any]]] = {}
    try:
        thresholds = None
        if args.parameter_thresholds:
            thresholds = json.loads(args.parameter_thresholds.read_text(encoding="utf-8"))
        for level in selected:
            try:
                if level == "L0":
                    level_results[level] = validate_manifest(manifest)
                elif level == "L1":
                    level_results[level] = run_l1(manifest, evidence_dir)
                elif level == "L2":
                    level_results[level] = run_l2(manifest)
                elif level == "L3":
                    level_results[level] = run_l3(manifest, evidence_dir, thresholds)
                else:
                    level_results[level] = [check("unknown_level", "FAIL",
                                                  evidence={"level": level})]
            except Exception as exc:
                level_results[level] = [check(f"{level.lower()}_exception", "BLOCKED",
                                              blocker=f"{type(exc).__name__}: {exc}")]
            current = run_levels(level_results, selected[:selected.index(level) + 1])
            if current["state"] in {"FAIL", "BLOCKED"}:
                break
    except Exception as exc:
        level = selected[0] if selected else "L0"
        level_results[level] = [check(f"{level.lower()}_exception", "BLOCKED",
                                      blocker=f"{type(exc).__name__}: {exc}")]
    report = build_report(str(manifest), level_results, selected)
    report["started_at"] = started.isoformat()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    if args.levels is not None and (args.pose or args.baseline_pose_png or
                                    args.baseline_rest_png or args.baseline_rest_sha or
                                    args.baseline_rest_opaque is not None):
        from rig_visual_gate import main as visual_main
        visual_dir = out.parent / "visual"
        visual_report_path = visual_dir / "report.json"
        visual_args = ["--manifest", args.manifest, "--out", str(visual_report_path)]
        for pose in args.pose:
            visual_args.extend(["--pose", pose])
        for option, value in (("--baseline-pose-png", args.baseline_pose_png),
                              ("--baseline-rest-png", args.baseline_rest_png),
                              ("--baseline-rest-sha", args.baseline_rest_sha),
                              ("--baseline-rest-opaque", args.baseline_rest_opaque)):
            if value is not None:
                visual_args.extend([option, str(value)])
        visual_args.extend(["--max-rest-changed", str(args.max_rest_changed),
                            "--max-new-transparent", str(args.max_new_transparent),
                            "--width", str(args.width), "--height", str(args.height)])
        try:
            visual_main(visual_args)
            report["visual"] = json.loads(visual_report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            report["visual"] = {"state": "FAIL", "blockers": [
                f"{type(exc).__name__}: {exc}"]}
            visual_dir.mkdir(parents=True, exist_ok=True)
            visual_report_path.write_text(json.dumps(report["visual"], ensure_ascii=False,
                                                     indent=2), encoding="utf-8")
        if report["visual"].get("state") == "FAIL":
            report["state"] = "FAIL"
        if report["visual"].get("state") == "BLOCKED" and report["state"] == "VALIDATED":
            report["state"] = "BLOCKED"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                              default=lambda value: value.tolist()
                              if hasattr(value, "tolist") else str(value)),
                   encoding="utf-8")
    print(f"{report['state']}: {out}")
    return 0 if report["state"] == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
