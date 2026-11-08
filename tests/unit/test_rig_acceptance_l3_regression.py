import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rig_acceptance as acceptance


def _manifest(tmp_path, parameters):
    path = tmp_path / "model.model3.json"
    path.write_text(json.dumps({"Parameters": parameters}), encoding="utf-8")
    return path


def _renderer(monkeypatch, frame_for):
    from drivers.live2d_runtime import moc3_verify

    calls = []

    def render(path, params=None, png=None, **kwargs):
        calls.append(dict(params or {}))
        frame, bbox = frame_for(dict(params or {}))
        Image.fromarray(frame, "RGBA").save(png)
        return {"ok": True, "alpha_bbox": bbox,
                "opaque_pixels": int(np.count_nonzero(frame[:, :, 3]))}

    monkeypatch.setattr(moc3_verify, "render_probe", render)
    return calls


def _frame(changed_x=None, changed_y=2):
    frame = np.zeros((8, 8, 4), dtype=np.uint8)
    frame[2:6, 2:6] = [40, 80, 120, 255]
    if changed_x is not None:
        frame[changed_y, changed_x] = [220, 20, 30, 255]
    return frame


def test_scans_every_parameter_while_fixing_other_parameters_at_default(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1},
        {"Id": "ParamB", "Minimum": 2, "Default": 3, "Maximum": 4},
    ])
    calls = _renderer(monkeypatch, lambda _: (_frame(), [2, 2, 5, 5]))
    acceptance.run_l3(manifest, tmp_path / "evidence")
    assert calls == [
        {"ParamA": 0.0, "ParamB": 3.0},
        {"ParamA": -1.0, "ParamB": 3.0},
        {"ParamA": 1.0, "ParamB": 3.0},
        {"ParamA": 0.0, "ParamB": 2.0},
        {"ParamA": 0.0, "ParamB": 3.0},
        {"ParamA": 0.0, "ParamB": 4.0},
    ]


def test_l3_uses_cubism_min_value_max_fields(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Min": -2, "Value": 0.5, "Max": 3}])
    calls = _renderer(monkeypatch, lambda _: (_frame(), [2, 2, 5, 5]))
    acceptance.run_l3(manifest, tmp_path / "evidence")
    assert calls == [
        {"ParamA": 0.5},
        {"ParamA": -2.0},
        {"ParamA": 3.0},
    ]


def test_l3_rejects_conflicting_default_and_value(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Min": -2, "Default": 0, "Value": 0.5, "Max": 3}])
    calls = _renderer(monkeypatch, lambda _: (_frame(), [2, 2, 5, 5]))
    checks = acceptance.run_l3(manifest, tmp_path / "evidence")
    assert calls == []
    assert checks[0]["id"] == "parameter_ranges"
    assert checks[0]["status"] == "FAIL"


def test_l3_rejects_conflicting_parameter_range_aliases(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [{
        "Id": "ParamA", "Min": -1, "Minimum": -2, "Default": 0,
        "Max": 1, "Maximum": 1,
    }])
    calls = _renderer(monkeypatch, lambda _: (_frame(), [2, 2, 5, 5]))
    checks = acceptance.run_l3(manifest, tmp_path / "evidence")
    assert calls == []
    assert checks[0]["id"] == "parameter_ranges"
    assert checks[0]["status"] == "FAIL"


def test_default_render_is_not_a_parameter_response_requirement(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1}])
    calls = _renderer(monkeypatch, lambda params: (
        _frame(6 if params["ParamA"] < 0 else 1 if params["ParamA"] > 0 else None),
        [2, 2, 6, 5] if params["ParamA"] else [2, 2, 5, 5]))
    checks = acceptance.run_l3(manifest, tmp_path / "evidence")
    responses = [item for item in checks if item["id"].startswith("parameter_response:")]
    assert len(calls) == 3
    assert {item["id"] for item in responses} == {"parameter_response:ParamA"}
    assert responses[0]["status"] == "PASS"


def test_both_extremes_without_response_fail_and_include_bbox_evidence(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1}])
    _renderer(monkeypatch, lambda _: (_frame(), [2, 2, 5, 5]))
    checks = acceptance.run_l3(manifest, tmp_path / "evidence")
    responses = [item for item in checks if item["id"].startswith("parameter_response:")]
    assert len(responses) == 1
    assert responses[0]["status"] == "FAIL"
    assert responses[0]["evidence"]["endpoints"]["min"]["changed_bbox"] is None
    assert responses[0]["evidence"]["endpoints"]["max"]["alpha_bbox"] == [2, 2, 5, 5]


def test_locality_threshold_is_applied_to_changed_bbox_area(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1}])
    _renderer(monkeypatch, lambda params: (
        _frame(6 if params["ParamA"] < 0 else 1 if params["ParamA"] > 0 else None,
               6 if params["ParamA"] < 0 else 1),
        [2, 2, 6, 6] if params["ParamA"] < 0 else [1, 1, 5, 5]))
    checks = acceptance.run_l3(
        manifest, tmp_path / "evidence", {"ParamA": {"max_changed_bbox_area": 0}})
    locality = next(item for item in checks if item["id"].startswith("parameter_locality:"))
    assert locality["status"] == "FAIL"
    assert locality["measured"]["max_changed_bbox_area"] == 1


def test_stale_default_png_is_not_used_as_baseline(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1}])
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    Image.fromarray(_frame(), "RGBA").save(evidence_dir / "default.png")
    _renderer(monkeypatch, lambda _: (_frame(), [2, 2, 5, 5]))
    checks = acceptance.run_l3(manifest, evidence_dir)
    responses = [item for item in checks if item["id"].startswith("parameter_response:")]
    assert len(responses) == 1
    assert responses[0]["status"] == "FAIL"
    assert Path(responses[0]["evidence"]["baseline_png"]).name != "default.png"


def test_l3_rejects_parameter_application_dictionary_containing_false(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1}])

    def render(path, params=None, png=None, **kwargs):
        Image.fromarray(_frame(), "RGBA").save(png)
        return {"ok": True, "parameters_applied": {"ParamA": False}}

    from drivers.live2d_runtime import moc3_verify
    monkeypatch.setattr(moc3_verify, "render_probe", render)
    checks = acceptance.run_l3(manifest, tmp_path / "evidence")
    scan = next(item for item in checks if item["id"] == "parameter_scan:ParamA")
    assert scan["status"] == "FAIL"


def test_l3_uses_fresh_render_directory_and_records_relative_png_paths(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, [
        {"Id": "ParamA", "Minimum": -1, "Default": 0, "Maximum": 1}])
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    Image.fromarray(_frame(), "RGBA").save(evidence_dir / "ParamA_default.png")
    from drivers.live2d_runtime import moc3_verify
    render_paths = []

    def capture_paths(path, params=None, png=None, **kwargs):
        render_paths.append(Path(png))
        frame = _frame(6 if params["ParamA"] < 0 else 1 if params["ParamA"] > 0 else None)
        bbox = [2, 2, 5, 5]
        Image.fromarray(frame, "RGBA").save(png)
        return {"ok": True, "alpha_bbox": bbox,
                "opaque_pixels": int(np.count_nonzero(frame[:, :, 3]))}

    monkeypatch.setattr(moc3_verify, "render_probe", capture_paths)
    checks = acceptance.run_l3(manifest, evidence_dir)
    assert len({path.parent for path in render_paths}) == 1
    assert render_paths[0].parent != evidence_dir
    responses = [item for item in checks if item["id"].startswith("parameter_response:")]
    assert responses[0]["evidence"]["baseline_png"]
    assert not Path(responses[0]["evidence"]["baseline_png"]).is_absolute()
    assert not Path(responses[0]["evidence"]["endpoints"]["min"]["png"]).is_absolute()
