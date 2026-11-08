import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rig_visual_gate as gate


def test_main_reads_sys_argv_when_argv_is_none(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["rig_visual_gate", "--levels", "L0"])
    monkeypatch.setattr(gate, "_acceptance_main", lambda argv: argv)

    assert gate.main() == ["--levels", "L0"]


def test_main_delegates_acceptance_arguments_without_legacy_options(monkeypatch):
    captured = []
    monkeypatch.setattr(gate, "_acceptance_main", lambda argv: captured.append(argv) or 17)

    result = gate.main(["--manifest", "model.json"])

    assert result == 17
    assert captured == [["--manifest", "model.json"]]


def test_main_delegates_levels_to_acceptance(monkeypatch):
    captured = []
    monkeypatch.setattr(gate, "_acceptance_main", lambda argv: captured.append(argv) or 23)

    result = gate.main(["--manifest", "model.json", "--levels", "L0,L1"])

    assert result == 23
    assert captured == [["--manifest", "model.json", "--levels", "L0,L1"]]


def test_main_runs_legacy_mode_without_delegating(monkeypatch):
    delegated = []
    monkeypatch.setattr(gate, "_acceptance_main", lambda argv: delegated.append(argv) or 99)
    monkeypatch.setattr(gate, "render_pose", lambda *args, **kwargs: {"ok": False, "blocker": "stub"})

    result = gate.main(["--manifest", "model.json", "--pose", "ParamAngleZ=30"])

    assert result == 1
    assert delegated == []
