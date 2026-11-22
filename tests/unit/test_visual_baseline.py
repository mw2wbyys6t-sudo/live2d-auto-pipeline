import json
import hashlib
from pathlib import Path

from tools.visual_baseline import build_report, load_dataset, validate_sample


def _manifest(tmp_path: Path, *, license_status="verified", evidence="license.txt"):
    source = tmp_path / "sample.png"
    source.write_bytes(b"synthetic fixture")
    return {
        "schema_version": 1,
        "samples": [{
            "id": "synthetic-front",
            "format": "png",
            "path": "sample.png",
            "sha256": "",
            "sample_kind": "synthetic",
            "rights_holder": "project-generated",
            "source": "generated-fixture",
            "license": {"id": "CC0-1.0", "status": license_status,
                        "evidence": evidence, "purposes": ["testing"],
                        "redistribution": "allowed"},
            "category": ["front_view"],
            "targets": ["segmentation", "pixel_provenance", "reconstruction", "alpha_integrity"],
            "annotation_version": "1",
            "actions": ["head_turn", "blink_mouth", "arm_elbow", "walk"],
        }],
    }


def test_synthetic_png_is_valid_and_actions_are_registered(tmp_path):
    doc = _manifest(tmp_path)
    doc["samples"][0]["sha256"] = hashlib.sha256(
        (tmp_path / "sample.png").read_bytes()).hexdigest()
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    sample = load_dataset(path)["samples"][0]
    checks = validate_sample(path, sample)

    assert all(item["status"] == "PASS" for item in checks)


def test_real_sample_without_license_evidence_is_blocked(tmp_path):
    doc = _manifest(tmp_path, license_status="unknown", evidence="")
    doc["samples"][0]["sample_kind"] = "real"
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    checks = validate_sample(path, doc["samples"][0])

    assert any(item["id"] == "license_evidence" and
               item["status"] == "BLOCKED" for item in checks)


def test_psd_structure_sample_requires_structure_targets(tmp_path):
    doc = _manifest(tmp_path)
    sample = doc["samples"][0]
    sample.update({"format": "psd", "path": "sample.psd",
                   "expected_structure": ["group_path", "layer_name", "canvas_offset",
                                          "draw_order", "visibility"]})
    (tmp_path / "sample.psd").write_bytes(b"synthetic psd structure")
    sample["sha256"] = hashlib.sha256(
        (tmp_path / "sample.psd").read_bytes()).hexdigest()
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    checks = validate_sample(path, sample)

    assert all(item["status"] == "PASS" for item in checks)


def test_dataset_report_blocks_unverified_real_sample(tmp_path):
    doc = _manifest(tmp_path, license_status="unknown", evidence="")
    doc["samples"][0]["sample_kind"] = "real"
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    report = build_report(path)

    assert report["state"] == "BLOCKED"
    assert report["required_actions"] == ["head_turn", "blink_mouth", "arm_elbow",
                                          "walk", "trunk_sway", "hair_clothing_secondary"]
