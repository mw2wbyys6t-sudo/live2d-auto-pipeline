import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rig_acceptance as acceptance
from drivers.live2d_runtime import moc3_verify


def test_core_status_classifies_explicit_probe_failure_as_fail():
    assert acceptance._core_status({"ok": False, "blocker": "bad moc"}) == "FAIL"


def test_core_status_classifies_exit_one_as_failure_and_exit_two_as_blocked():
    assert acceptance._core_status({"ok": False, "exit_code": 1}) == "FAIL"
    assert acceptance._core_status({"ok": False, "exit_code": 2}) == "BLOCKED"
    assert acceptance._core_status({"ok": False, "environment_blocked": True,
                                    "blocker": "Cubism SDK is missing"}) == "BLOCKED"


def test_core_status_classifies_timeout_and_crash_as_blocked():
    assert acceptance._core_status({"ok": False, "timed_out": True}) == "BLOCKED"
    assert acceptance._core_status({"ok": False, "crashed": True}) == "BLOCKED"


def test_core_status_classifies_environment_blocker_as_blocked():
    assert acceptance._core_status({"ok": False, "environment_blocked": True,
                                    "blocker": "runtime unavailable"}) == "BLOCKED"


def test_l1_empty_first_frame_is_fail(monkeypatch, tmp_path):
    manifest = tmp_path / "model.model3.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(moc3_verify, "verify_moc3_consistency", lambda *_: {"ok": True})
    monkeypatch.setattr(moc3_verify, "verify_moc3_load", lambda *_: {"ok": True})

    def render_probe(*args, png, **kwargs):
        from PIL import Image
        Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(png)
        return {"ok": True, "stdout": "render out", "stderr": "render err",
                "opaque_pixels": 0, "alpha_bbox": None}

    monkeypatch.setattr(moc3_verify, "render_probe", render_probe)
    checks = acceptance.run_l1(manifest, tmp_path / "evidence")
    result = next(item for item in checks if item["id"] == "default_render")
    assert result["status"] == "FAIL"


def test_l1_saves_probe_stdout_and_stderr(monkeypatch, tmp_path):
    manifest = tmp_path / "model.model3.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(moc3_verify, "verify_moc3_consistency",
                        lambda *_: {"ok": False, "blocker": "inconsistent", "stdout": "consistency out", "stderr": "consistency err"})
    monkeypatch.setattr(moc3_verify, "verify_moc3_load",
                        lambda *_: {"ok": False, "blocker": "invalid", "stdout": "load out", "stderr": "load err"})

    def render_probe(*args, png, **kwargs):
        from PIL import Image
        Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(png)
        return {"ok": True, "stdout": "render out", "stderr": "render err",
                "opaque_pixels": 4, "alpha_bbox": [0, 0, 1, 1]}

    monkeypatch.setattr(moc3_verify, "render_probe", render_probe)
    evidence = tmp_path / "evidence"
    acceptance.run_l1(manifest, evidence)

    saved = json.loads((evidence / "probe_logs.json").read_text(encoding="utf-8"))
    assert saved["core_consistency"] == {"stdout": "consistency out", "stderr": "consistency err"}
    assert saved["core_load"] == {"stdout": "load out", "stderr": "load err"}
    assert saved["default_render"] == {"stdout": "render out", "stderr": "render err"}
