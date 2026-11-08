import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rig_acceptance as acceptance


def check(check_id, status, evidence=None):
    return {"id": check_id, "status": status, "evidence": evidence}


def make_manifest(tmp_path, moc="model.moc3", texture="texture.png", parameters=None):
    (tmp_path / "model.moc3").write_bytes(b"moc")
    from PIL import Image
    Image.new("RGBA", (2, 2)).save(tmp_path / "texture.png")
    payload = {
        "Version": 3,
        "FileReferences": {"Moc": moc, "Textures": [texture]},
        "Groups": [{"Target": "Parameter", "Name": "EyeBlink",
                    "Ids": [p["Id"] for p in (parameters or [])]}],
        "Parameters": parameters or [],
    }
    path = tmp_path / "model.model3.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_blocked_required_check_blocks_level_and_overall():
    report = acceptance.run_levels(
        {"L0": [check("schema", "BLOCKED")]}, levels=["L0", "L1"])
    assert report["levels"]["L0"]["status"] == "BLOCKED"
    assert report["levels"]["L1"]["status"] == "BLOCKED"
    assert report["state"] == "BLOCKED"


def test_failure_blocks_later_levels_and_keeps_failure_evidence():
    report = acceptance.run_levels(
        {"L0": [check("manifest", "FAIL", {"path": "x"})]},
        levels=["L0", "L1"])
    assert report["levels"]["L0"]["checks"][0]["evidence"] == {"path": "x"}
    assert report["levels"]["L1"]["status"] == "BLOCKED"
    assert report["state"] == "FAIL"


def test_l0_reports_invalid_json(tmp_path):
    path = tmp_path / "model.model3.json"
    path.write_text("{", encoding="utf-8")
    checks = acceptance.validate_manifest(path)
    assert checks[0]["status"] == "FAIL"
    assert checks[0]["id"] == "manifest_json"


def test_l0_rejects_missing_and_escaping_references(tmp_path):
    manifest = make_manifest(tmp_path, moc="../outside.moc3", texture="missing.png")
    checks = {item["id"]: item for item in acceptance.validate_manifest(manifest)}
    assert checks["reference_path_contained"]["status"] == "FAIL"
    assert checks["referenced_files_exist"]["status"] == "FAIL"


def test_l0_accepts_valid_manifest_and_parameter_bounds(tmp_path):
    manifest = make_manifest(tmp_path, parameters=[
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1}])
    checks = acceptance.validate_manifest(manifest)
    assert checks
    assert all(item["status"] == "PASS" for item in checks)


def test_l0_accepts_cubism_value_min_max_parameters(tmp_path):
    manifest = make_manifest(tmp_path, parameters=[
        {"Id": "ParamA", "Value": 0, "Min": -1, "Max": 1}])
    checks = {item["id"]: item for item in acceptance.validate_manifest(manifest)}
    assert checks["parameter_ranges"]["status"] == "PASS"


def test_l2_finds_bad_indices_degenerate_triangles_and_uvs():
    mesh = {"id": "DrawableA", "vertices": [[0, 0], [1, 0], [2, 0]],
            "indices": [[0, 1, 3]],
            "uvs": [[0, 0], [1, 0], [float("nan"), 1]],
            "texture_page": 0}
    checks = {item["id"]: item for item in acceptance.validate_meshes([mesh])}
    assert checks["mesh_indices"]["status"] == "FAIL"
    assert checks["mesh_degenerate_triangles"]["status"] == "FAIL"
    assert checks["mesh_uv_finite"]["status"] == "FAIL"


def test_l2_missing_mesh_evidence_is_blocked(tmp_path):
    checks = acceptance.load_mesh_evidence(tmp_path / "absent.meshes.json")
    assert checks[0]["status"] == "BLOCKED"


@pytest.mark.parametrize("meshes", [None, {}, [None], ["bad"]])
def test_l2_malformed_mesh_inputs_return_fail_checks(meshes):
    checks = acceptance.validate_meshes(meshes)
    assert checks
    assert all(item["status"] == "FAIL" for item in checks)


def test_l2_malformed_mesh_fields_are_reported():
    checks = acceptance.validate_meshes([{"vertices": 3}])
    assert next(item for item in checks if item["id"] == "mesh_evidence")["status"] == "FAIL"


def test_l2_empty_and_non_finite_geometry_fail():
    empty = acceptance.validate_meshes([{
        "id": "empty", "vertices": [], "indices": [], "uvs": [], "texture_page": 0
    }])
    checks = {item["id"]: item["status"] for item in empty}
    assert checks["mesh_indices"] == "FAIL"
    assert checks["mesh_degenerate_triangles"] == "FAIL"

    non_finite = acceptance.validate_meshes([{
        "id": "nan", "vertices": [[0, 0], [1, 0], [float("inf"), 1]],
        "indices": [[0, 1, 2]], "uvs": [[0, 0], [1, 0], [0, 1]],
        "texture_page": 0,
    }])
    checks = {item["id"]: item["status"] for item in non_finite}
    assert checks["mesh_indices"] == "FAIL"
    assert checks["mesh_degenerate_triangles"] == "FAIL"


def test_l2_uv_missing_is_blocked(tmp_path):
    evidence = tmp_path / "model.meshes.json"
    evidence.write_text(json.dumps({"DrawableA": {
        "vertices": [[0, 0], [1, 0], [2, 0]], "indices": [[0, 1, 3]],
        "texture_page": 0,
    }}), encoding="utf-8")
    checks = acceptance.load_mesh_evidence(evidence)
    statuses = {item["id"]: item["status"] for item in checks}
    assert statuses["mesh_indices"] == "FAIL"
    assert statuses["mesh_degenerate_triangles"] == "FAIL"
    assert statuses["mesh_uv_finite"] == "BLOCKED"


def test_l2_missing_texture_page_blocks_only_page_check(tmp_path):
    evidence = tmp_path / "model.meshes.json"
    evidence.write_text(json.dumps({"DrawableA": {
        "vertices": [[0, 0], [1, 0], [0, 1]], "indices": [[0, 1, 2]],
        "uvs": [[0, 0], [1, 0], [0, 1]],
    }}), encoding="utf-8")
    checks = acceptance.load_mesh_evidence(evidence)
    statuses = {item["id"]: item["status"] for item in checks}
    assert statuses["mesh_indices"] == "PASS"
    assert statuses["mesh_texture_page"] == "BLOCKED"


def test_l2_uses_manifest_texture_page_count(tmp_path):
    manifest = make_manifest(tmp_path)
    evidence = tmp_path / "model.meshes.json"
    evidence.write_text(json.dumps({"DrawableA": {
        "vertices": [[0, 0], [1, 0], [0, 1]], "indices": [[0, 1, 2]],
        "uvs": [[0, 0], [1, 0], [0, 1]], "texture_page": 1,
    }}), encoding="utf-8")
    checks = acceptance.run_l2(manifest)
    assert next(item for item in checks if item["id"] == "mesh_texture_page")["status"] == "FAIL"


@pytest.mark.parametrize("contents", ["{", "[]", "{\"FileReferences\": null}"])
def test_l2_malformed_manifest_returns_failure(contents, tmp_path):
    manifest = tmp_path / "model.model3.json"
    manifest.write_text(contents, encoding="utf-8")
    checks = acceptance.run_l2(manifest)
    assert checks
    assert all(item["status"] == "FAIL" for item in checks)


def test_l1_records_consistency_load_and_default_render(monkeypatch, tmp_path):
    from drivers.live2d_runtime import moc3_verify
    from PIL import Image
    monkeypatch.setattr(moc3_verify, "verify_moc3_consistency",
                        lambda *_: {"ok": True})
    monkeypatch.setattr(moc3_verify, "verify_moc3_load",
                        lambda *_: {"ok": True, "parameter_ids": ["ParamAngleX"]})

    def render_probe(*args, png, **kwargs):
        Image.new("RGBA", (6, 6), (0, 0, 0, 255)).save(png)
        return {"ok": True, "opaque_pixels": 36, "alpha_bbox": [0, 0, 5, 5]}

    monkeypatch.setattr(moc3_verify, "render_probe", render_probe)
    manifest = tmp_path / "model.model3.json"
    manifest.write_text("{}", encoding="utf-8")
    checks = acceptance.run_l1(manifest, tmp_path / "evidence")
    assert all(item["status"] == "PASS" for item in checks)


def test_l3_scans_each_declared_parameter_at_min_default_max(monkeypatch, tmp_path):
    from drivers.live2d_runtime import moc3_verify
    manifest = make_manifest(tmp_path, parameters=[
        {"Id": "A", "Minimum": -1, "Default": 0, "Maximum": 1},
        {"Id": "B", "Minimum": 0, "Default": 0.5, "Maximum": 2},
    ])
    calls = []

    def render(path, params=None, png=None, **kwargs):
        calls.append((params, png))
        Path(png).parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new("RGBA", (8, 8), (0, 0, 0, 255)).save(png)
        return {"ok": True, "opaque_pixels": 64, "alpha_bbox": [0, 0, 7, 7]}

    monkeypatch.setattr(moc3_verify, "render_probe", render)
    checks = acceptance.run_l3(manifest, tmp_path / "renders")
    assert len(calls) == 6
    assert [call[0] for call in calls] == [
        {"A": 0.0, "B": 0.5}, {"A": -1.0, "B": 0.5},
        {"A": 1.0, "B": 0.5}, {"A": 0.0, "B": 0.0},
        {"A": 0.0, "B": 0.5}, {"A": 0.0, "B": 2.0},
    ]
    responses = [item for item in checks if item["id"].startswith("parameter_response:")]
    assert all(item["status"] == "FAIL" for item in responses)


def test_l3_rejects_parameters_not_applied_by_core(monkeypatch, tmp_path):
    from drivers.live2d_runtime import moc3_verify
    manifest = make_manifest(tmp_path, parameters=[
        {"Id": "A", "Minimum": -1, "Default": 0, "Maximum": 1}])

    def render(path, params=None, png=None, **kwargs):
        from PIL import Image
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(png)
        return {"ok": True, "parameters_applied": False, "centroid": [2, 3]}

    monkeypatch.setattr(moc3_verify, "render_probe", render)
    checks = acceptance.run_l3(manifest, tmp_path / "renders")
    scan = next(item for item in checks if item["id"] == "parameter_scan:A")
    assert scan["status"] == "FAIL"
    assert scan["evidence"]["parameters_applied"] == [False, False, False]


@pytest.mark.parametrize("threshold", [-1, "not-a-number", float("nan"), float("inf")])
def test_l3_rejects_invalid_locality_thresholds(monkeypatch, tmp_path, threshold):
    from drivers.live2d_runtime import moc3_verify
    manifest = make_manifest(tmp_path, parameters=[
        {"Id": "ParamAngleX", "Minimum": -1, "Default": 0, "Maximum": 1}])

    def render(path, params=None, png=None, **kwargs):
        from PIL import Image
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(png)
        return {"ok": True, "parameters_applied": True}

    monkeypatch.setattr(moc3_verify, "render_probe", render)
    checks = acceptance.run_l3(
        manifest, tmp_path / "renders", {"ParamAngleX": threshold})

    locality = next(item for item in checks
                    if item["id"] == "parameter_locality:ParamAngleX")
    assert locality["status"] == "BLOCKED"


def test_l3_does_not_accept_png_when_core_did_not_apply_parameters(monkeypatch, tmp_path):
    from drivers.live2d_runtime import moc3_verify
    manifest = make_manifest(tmp_path, parameters=[
        {"Id": "ParamAngleX", "Minimum": -1, "Default": 0, "Maximum": 1}])

    def render(path, params=None, png=None, **kwargs):
        from PIL import Image
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(png)
        return {"ok": True, "parameters_applied": False}

    monkeypatch.setattr(moc3_verify, "render_probe", render)
    checks = acceptance.run_l3(manifest, tmp_path / "renders")

    scan = next(item for item in checks if item["id"] == "parameter_scan:ParamAngleX")
    assert scan["status"] == "FAIL"
    assert scan["evidence"]["parameters_applied"] == [False, False, False]


def test_l3_evidence_includes_centroid_and_unique_sanitized_filenames(monkeypatch, tmp_path):
    from drivers.live2d_runtime import moc3_verify
    manifest = make_manifest(tmp_path, parameters=[
        {"Id": "A/B", "Minimum": -1, "Default": 0, "Maximum": 1},
        {"Id": "A?B", "Minimum": -1, "Default": 0, "Maximum": 1},
    ])
    paths = []

    def render(path, params=None, png=None, **kwargs):
        paths.append(png)
        from PIL import Image
        Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(png)
        return {"ok": True, "parameters_applied": True, "centroid": [1.5, 1.5]}

    monkeypatch.setattr(moc3_verify, "render_probe", render)
    checks = acceptance.run_l3(manifest, tmp_path / "renders")
    assert len(paths) == len(set(paths)) == 6
    responses = [item for item in checks if item["id"].startswith("parameter_response:")]
    assert all(item["evidence"]["baseline_centroid"] == [1.5, 1.5]
               for item in responses)


def test_cli_writes_report_even_when_blocked(tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    result = acceptance.main(["--manifest", str(tmp_path / "missing.model3.json"),
                              "--out", str(report_path)])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["state"] in {"FAIL", "BLOCKED"}
    assert result == 1


def test_cli_defaults_to_ordered_levels_and_timestamps(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    monkeypatch.setattr(acceptance, "run_l1", lambda *_: [check("l1", "PASS")])
    monkeypatch.setattr(acceptance, "run_l2", lambda *_: [check("l2", "PASS")])
    monkeypatch.setattr(acceptance, "run_l3", lambda *_: [check("l3", "PASS")])
    output = tmp_path / "report.json"

    assert acceptance.main(["--manifest", str(manifest), "--out", str(output)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert list(report["levels"]) == ["L0", "L1", "L2", "L3"]
    assert report["started_at"] <= report["finished_at"]


def test_cli_writes_exception_report(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    monkeypatch.setattr(acceptance, "run_l1", lambda *_: (_ for _ in ()).throw(RuntimeError("probe failed")))
    output = tmp_path / "report.json"

    assert acceptance.main(["--manifest", str(manifest), "--levels", "L1", "--out", str(output)]) == 1

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["state"] == "BLOCKED"
    assert "probe failed" in report["levels"]["L1"]["checks"][0]["blocker"]


def test_cli_attaches_baseline_visual_report(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    monkeypatch.setattr(acceptance, "run_l1", lambda *_: [check("l1", "PASS")])
    monkeypatch.setattr(acceptance, "run_l2", lambda *_: [check("l2", "PASS")])
    monkeypatch.setattr(acceptance, "run_l3", lambda *_: [check("l3", "PASS")])
    monkeypatch.setattr("rig_visual_gate.render_pose", lambda *_: {"ok": False, "blocker": "no core"})
    output = tmp_path / "report.json"

    acceptance.main(["--manifest", str(manifest), "--levels", "L3,L1,L0,L2",
                     "--pose", "ParamAngleZ=5", "--baseline-rest-png", str(tmp_path / "base.png"),
                     "--out", str(output)])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert list(report["levels"]) == ["L0", "L1", "L2", "L3"]
    assert report["visual"]["state"] == "FAIL"
    assert report["visual"]["started_at"] <= report["visual"]["finished_at"]
