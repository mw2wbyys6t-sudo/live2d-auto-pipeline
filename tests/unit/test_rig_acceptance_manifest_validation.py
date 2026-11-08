import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))

import rig_acceptance as acceptance


def make_manifest(tmp_path, payload):
    (tmp_path / "model.moc3").write_bytes(b"moc")
    (tmp_path / "texture.png").write_bytes(b"png")
    path = tmp_path / "model.model3.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def statuses(path):
    return {item["id"]: item["status"]
            for item in acceptance.validate_manifest(path)}


def test_validates_nested_file_references(tmp_path):
    path = make_manifest(tmp_path, {
        "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]},
        "Motions": {"Idle": [{"File": "missing.motion3.json"}]},
    })
    result = statuses(path)
    assert result["referenced_files_exist"] == "FAIL"


def test_rejects_invalid_manifest_shapes_and_texture_references(tmp_path):
    path = make_manifest(tmp_path, {
        "FileReferences": {"Moc": "model.moc3", "Textures": "texture.png"},
        "Parameters": {},
    })
    result = statuses(path)
    assert result["manifest_schema"] == "FAIL"
    assert result["texture_references"] == "FAIL"


def test_rejects_non_finite_parameter_values(tmp_path):
    path = make_manifest(tmp_path, {
        "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]},
        "Parameters": [{"Id": "ParamA", "Minimum": 0,
                        "Default": float("nan"), "Maximum": 1}],
    })
    result = statuses(path)
    assert result["parameter_ranges"] == "FAIL"


def test_rejects_duplicate_parameter_ids(tmp_path):
    path = make_manifest(tmp_path, {
        "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]},
        "Parameters": [
            {"Id": "ParamA", "Minimum": 0, "Default": 0, "Maximum": 1},
            {"Id": "ParamA", "Minimum": 0, "Default": 0, "Maximum": 1},
        ],
    })
    result = statuses(path)
    assert result["parameter_ids_unique"] == "FAIL"


def test_accepts_cubism_parameter_fields_and_rejects_conflicting_aliases(tmp_path):
    path = make_manifest(tmp_path, {
        "Version": 3,
        "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]},
        "Parameters": [
            {"Id": "ParamA", "Min": -1, "Default": 0, "Max": 1, "Value": 0}
        ],
        "Groups": [],
    })
    assert statuses(path)["parameter_ranges"] == "PASS"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["Parameters"][0].update({"Minimum": -2, "Maximum": 1})
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert statuses(path)["parameter_ranges"] == "FAIL"


def test_checks_additional_file_references_and_png_readability(tmp_path):
    (tmp_path / "model.moc3").write_bytes(b"moc")
    (tmp_path / "texture.png").write_bytes(b"not a png")
    for filename in ("physics.json", "pose.json", "userdata.json", "display.json"):
        (tmp_path / filename).write_text("{}", encoding="utf-8")
    path = tmp_path / "model.model3.json"
    path.write_text(json.dumps({
        "Version": 3,
        "FileReferences": {
            "Moc": "model.moc3",
            "Textures": ["texture.png"],
            "Physics": "physics.json",
            "Pose": "pose.json",
            "UserData": "userdata.json",
            "DisplayInfo": "display.json",
        },
        "Parameters": [],
        "Groups": [],
    }), encoding="utf-8")
    result = statuses(path)
    assert result["referenced_files_exist"] == "PASS"
    assert result["texture_references"] == "FAIL"


def test_requires_version_and_rejects_duplicate_group_ids(tmp_path):
    path = make_manifest(tmp_path, {
        "FileReferences": {"Moc": "model.moc3", "Textures": ["texture.png"]},
        "Parameters": [],
        "Groups": [
            {"Target": "Parameter", "Ids": ["ParamA", "ParamB"]},
            {"Target": "Parameter", "Ids": ["ParamA"]},
        ],
    })
    result = statuses(path)
    assert result["manifest_schema"] == "FAIL"
    assert result["group_ids_unique"] == "FAIL"
