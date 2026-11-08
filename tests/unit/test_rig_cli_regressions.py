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
import rig_visual_gate as visual_gate


def test_acceptance_keeps_visual_failure_in_overall_state_and_report(tmp_path, monkeypatch):
    manifest = tmp_path / "model.model3.json"
    manifest.write_text("{}", encoding="utf-8")
    for level in ("run_l1", "run_l2", "run_l3"):
        monkeypatch.setattr(acceptance, level, lambda *_args: [
            {"id": "check", "status": "PASS"}])
    monkeypatch.setattr("rig_visual_gate.render_pose", lambda *_args: {
        "ok": False, "blocker": "test render failure"})
    output = tmp_path / "report.json"

    result = acceptance.main([
        "--manifest", str(manifest), "--levels", "L0,L1,L2,L3",
        "--pose", "ParamAngleZ=5", "--out", str(output)])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["visual"]["state"] == "FAIL"
    assert report["state"] == "FAIL"
    assert result == 1
    assert (tmp_path / "visual" / "report.json").is_file()


def test_visual_cli_writes_failure_report_when_render_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(visual_gate, "render_pose", lambda *_args: (_ for _ in ()).throw(
        RuntimeError("render crashed")))
    output = tmp_path / "report.json"

    result = visual_gate.main(["--manifest", "missing.model3.json", "--pose", "ParamAngleZ=5",
                               "--out", str(output)])

    assert result == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["state"] == "FAIL"
    assert "render crashed" in " ".join(report["blockers"])


def test_legacy_baseline_rest_sha_option_remains_available(tmp_path, monkeypatch):
    monkeypatch.setattr(visual_gate, "render_pose", lambda *_args: {
        "ok": False, "blocker": "no core"})
    output = tmp_path / "report.json"

    assert visual_gate.main([
        "--manifest", "unused", "--baseline-rest-sha", "legacy-sha",
        "--out", str(output)]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert next(item for item in report["checks"]
                if item["id"] == "rest_sha_match")["status"] == "INFO"


def test_legacy_visual_mode_requires_explicit_pose_or_baseline(tmp_path, monkeypatch):
    manifest = tmp_path / "model.model3.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(acceptance, "validate_manifest", lambda *_: [
        {"id": "check", "status": "PASS"}])
    monkeypatch.setattr(acceptance, "run_l1", lambda *_: [
        {"id": "check", "status": "PASS"}])
    monkeypatch.setattr(acceptance, "run_l2", lambda *_: [
        {"id": "check", "status": "PASS"}])
    monkeypatch.setattr(acceptance, "run_l3", lambda *_: [
        {"id": "check", "status": "PASS"}])
    visual_calls = []
    monkeypatch.setattr("rig_visual_gate.main", lambda argv: visual_calls.append(argv) or 1)

    result = acceptance.main(["--manifest", str(manifest), "--out",
                              str(tmp_path / "report.json")])

    assert result == 0
    assert visual_calls == []
    assert acceptance.main(["--manifest", str(manifest), "--pose", "ParamAngleZ=5",
                             "--out", str(tmp_path / "legacy-report.json")]) == 1
    assert len(visual_calls) == 1


def test_changed_pixels_rejects_mismatched_rgba_dimensions():
    base = np.zeros((2, 3, 4), dtype=np.uint8)
    current = np.zeros((3, 3, 4), dtype=np.uint8)

    try:
        visual_gate.changed_pixels(base, current)
    except ValueError:
        pass
    else:
        raise AssertionError("dimension mismatch must not be compared")


def test_visual_cli_marks_incompatible_baseline_rgba_blocked(tmp_path, monkeypatch):
    base = np.zeros((2, 3, 4), dtype=np.uint8)
    current = np.zeros((3, 3, 4), dtype=np.uint8)
    baseline = tmp_path / "baseline.png"
    Image.fromarray(base, "RGBA").save(baseline)
    calls = iter([current, current])

    def render(_manifest, _values, png, _width, _height):
        Image.fromarray(next(calls), "RGBA").save(png)
        return {"ok": True}

    monkeypatch.setattr(visual_gate, "render_pose", render)
    output = tmp_path / "report.json"
    result = visual_gate.main([
        "--manifest", "unused", "--baseline-rest-png", str(baseline),
        "--out", str(output)])

    report = json.loads(output.read_text(encoding="utf-8"))
    diff_check = next(item for item in report["checks"]
                      if item["id"] == "rest_changed_px")
    assert result == 1
    assert diff_check["status"] == "BLOCKED"
