from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ACTION_GROUPS = {
    "head_turn": "head_turn",
    "blink_mouth": "blink_mouth",
    "arm_elbow": "arm_elbow",
    "walk": "walk",
    "trunk_sway": "trunk_sway",
    "hair_clothing_secondary": "hair_clothing_secondary",
}
PSD_STRUCTURE_TARGETS = {"group_path", "layer_name", "canvas_offset", "draw_order", "visibility"}
PNG_TARGETS = {"segmentation", "pixel_provenance", "reconstruction", "alpha_integrity"}


def _check(check_id: str, status: str, evidence: Any = None) -> dict[str, Any]:
    return {"id": check_id, "status": status, "evidence": evidence}


def load_dataset(path: Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("samples"), list):
        raise ValueError("dataset manifest must have schema_version=1 and a samples array")
    return doc


def validate_sample(manifest_path: Path, sample: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    checks: list[dict[str, Any]] = []
    sample_id = sample.get("id")
    if not isinstance(sample_id, str) or not sample_id.strip():
        checks.append(_check("sample_id", "FAIL"))
    else:
        checks.append(_check("sample_id", "PASS", sample_id))

    relative = sample.get("path")
    path = (root / relative).resolve() if isinstance(relative, str) else root
    contained = path.is_relative_to(root)
    exists = contained and path.is_file()
    checks.append(_check("sample_path", "PASS" if exists else "BLOCKED" if not contained else "FAIL", str(relative)))
    if exists:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checks.append(_check("sha256", "PASS" if sample.get("sha256") == digest else "FAIL",
                             {"expected": sample.get("sha256"), "actual": digest}))
    else:
        checks.append(_check("sha256", "BLOCKED", "sample file unavailable"))

    license_info = sample.get("license") if isinstance(sample.get("license"), dict) else {}
    evidence = license_info.get("evidence")
    evidence_path = (root / evidence).resolve() if isinstance(evidence, str) and evidence else None
    evidence_exists = bool(evidence_path and evidence_path.is_relative_to(root) and evidence_path.is_file())
    license_ok = (license_info.get("status") == "verified" and
                  isinstance(license_info.get("id"), str) and bool(license_info.get("id")) and
                  isinstance(license_info.get("purposes"), list) and bool(license_info["purposes"]) and
                  license_info.get("redistribution") in {"allowed", "restricted", "prohibited"})
    if sample.get("sample_kind") == "synthetic":
        checks.append(_check("license_evidence", "PASS" if license_ok else "FAIL",
                             "synthetic sample must have explicit scope"))
    else:
        checks.append(_check("license_evidence", "PASS" if license_ok and evidence_exists else "BLOCKED",
                             str(evidence) if evidence_exists else "verified license evidence required"))

    fmt = str(sample.get("format", "")).lower()
    targets = sample.get("expected_structure") if fmt == "psd" else sample.get("targets")
    required = PSD_STRUCTURE_TARGETS if fmt == "psd" else PNG_TARGETS if fmt == "png" else set()
    targets_ok = isinstance(targets, list) and required <= set(targets)
    checks.append(_check("format_targets", "PASS" if targets_ok else "FAIL",
                         {"format": fmt, "required": sorted(required), "provided": targets}))

    actions = sample.get("actions", [])
    action_ok = isinstance(actions, list) and all(item in ACTION_GROUPS for item in actions)
    checks.append(_check("action_scope", "PASS" if action_ok else "FAIL", actions))
    annotation_ok = isinstance(sample.get("annotation_version"), str) and bool(sample["annotation_version"])
    checks.append(_check("annotation_version", "PASS" if annotation_ok else "FAIL",
                         sample.get("annotation_version")))
    return checks


def build_report(path: Path) -> dict[str, Any]:
    doc = load_dataset(path)
    ids = [sample.get("id") for sample in doc["samples"] if isinstance(sample, dict)]
    results = []
    for sample in doc["samples"]:
        if not isinstance(sample, dict):
            results.append({"id": None, "state": "FAIL", "checks": [_check("sample", "FAIL")]})
            continue
        checks = validate_sample(path, sample)
        license_blocked = any(item["id"] == "license_evidence" and
                              item["status"] == "BLOCKED" for item in checks)
        state = ("BLOCKED" if license_blocked else
                 "FAIL" if any(item["status"] == "FAIL" for item in checks) else
                 "BLOCKED" if any(item["status"] == "BLOCKED" for item in checks) else "PASS")
        results.append({"id": sample.get("id"), "state": state, "checks": checks})
    unique = len(ids) == len(set(ids))
    state = "FAIL" if not unique or any(item["state"] == "FAIL" for item in results) else (
        "BLOCKED" if any(item["state"] == "BLOCKED" for item in results) else "PASS")
    return {"schema_version": 1, "manifest": str(path), "state": state,
            "unique_sample_ids": unique, "samples": results,
            "required_actions": list(ACTION_GROUPS)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a visual baseline dataset manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = build_report(args.manifest)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["state"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
