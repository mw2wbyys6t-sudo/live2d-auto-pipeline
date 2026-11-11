"""compatibility 语义契约：false 必须由 status 解释，不能与 runtime_ready 冲突。

运行时清单只能证明「官方 Core 能加载 moc3」，证明不了 Cubism Editor 工程兼容，
也证明不了 VTube Studio 实机加载；VSeeFace 则根本不读 model3/moc3。
因此 ``compatible`` 为 false 时必须有可读的 ``status``。
"""

import json

from live2d_builder.validator.model_validator import ModelValidator


def test_missing_model3_reports_missing_artifact(tmp_path):
    result = ModelValidator().check_cubism_compatibility(str(tmp_path))
    assert set(result) == {"cubism_editor", "vtube_studio", "vseeface"}
    for item in result.values():
        assert item["compatible"] is False
        assert item["status"] == "missing_artifact"
        assert item["notes"]


def test_false_compatible_is_explained_by_a_status(tmp_path):
    (tmp_path / "c.model3.json").write_text(
        json.dumps({"Version": 3, "FileReferences": {"Moc": "c.moc3", "Textures": []},
                    "Groups": [], "Parameters": []}),
        encoding="utf-8",
    )
    result = ModelValidator().check_cubism_compatibility(str(tmp_path))

    assert result["cubism_editor"]["status"] == "not_applicable"
    assert result["vtube_studio"]["status"] == "unverified"
    assert result["vseeface"]["status"] == "unsupported"
    for item in result.values():
        assert item["compatible"] is False
        assert item["status"] not in ("", "unknown")
