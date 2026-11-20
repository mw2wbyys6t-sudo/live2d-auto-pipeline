import json

from PIL import Image
from psd_tools import PSDImage

from core.psd.creator import PSDCreator
from core.psd.validator import validate_psd_roundtrip


def _manifest_layers(tmp_path):
    layers = tmp_path / "layers"
    layers.mkdir()
    back = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
    back.putpixel((2, 3), (255, 0, 0, 255))
    front = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    front.putpixel((5, 4), (0, 0, 255, 128))
    back.save(layers / "back.png")
    front.save(layers / "front.png")
    manifest = {
        "canvas": {"width": 8, "height": 8},
        "groups": [
            {"id": "body", "name_zh": "身体", "psd_name": "body", "visible": True, "order": 1},
            {"id": "face", "name_zh": "面部", "psd_name": "face", "visible": False, "order": 2},
        ],
        "draw_order": ["back", "front"],
        "layers": [
            {"id": "back", "file": "back.png", "group": "body", "bbox": [2, 3, 2, 3], "visible": True},
            {"id": "front", "file": "front.png", "group": "face", "bbox": [5, 4, 5, 4], "visible": True},
        ],
    }
    path = tmp_path / "layer_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return layers, path, manifest


def test_manifest_groups_are_saved_and_verified_from_real_psd(tmp_path):
    layers, manifest_path, manifest = _manifest_layers(tmp_path)
    output = tmp_path / "grouped.psd"

    result = PSDCreator().create_psd(str(layers), str(output), manifest_path=str(manifest_path))

    assert result["success"] is True
    assert result["fallback"] is False
    psd = PSDImage.open(output)
    assert psd.size == (8, 8)
    groups = {group.name: group for group in psd}
    assert set(groups) == {"body", "face"}
    assert groups["body"].visible is True
    assert groups["face"].visible is False
    assert [layer.name for layer in groups["body"]] == ["back"]
    assert [layer.name for layer in groups["face"]] == ["front"]
    checked = validate_psd_roundtrip(str(output), manifest,
                                     layer_files=[layers / "back.png", layers / "front.png"])
    assert checked["valid"] is True
    assert checked["errors"] == []


def test_png_fallback_cannot_report_psd_success(tmp_path, monkeypatch):
    layers = tmp_path / "layers"
    layers.mkdir()
    Image.new("RGBA", (4, 4), "red").save(layers / "layer_00.png")
    creator = PSDCreator()
    monkeypatch.setattr(creator, "_has_psd_tools", False)

    result = creator.create_psd(str(layers), str(tmp_path / "missing.psd"))

    assert result["success"] is False
    assert result["fallback"] is True
    assert not (tmp_path / "missing.psd").exists()


def test_manifest_rejects_unsupported_layer_features(tmp_path):
    layers, manifest_path, manifest = _manifest_layers(tmp_path)
    manifest["layers"][0]["blend_mode"] = "multiply"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = PSDCreator().create_psd(str(layers), str(tmp_path / "unsupported.psd"),
                                     manifest_path=str(manifest_path))

    assert result["success"] is False
    assert "unsupported" in result["error"].lower()
    assert not (tmp_path / "unsupported.psd").exists()


def test_manifest_preserves_unicode_semantic_names_with_ascii_psd_identifiers(tmp_path):
    layers, manifest_path, manifest = _manifest_layers(tmp_path)
    output = tmp_path / "unicode.psd"
    result = PSDCreator().create_psd(str(layers), str(output),
                                     manifest_path=str(manifest_path))

    assert result["success"] is True
    psd = PSDImage.open(output)
    assert psd[0].name == "body"
    assert manifest["groups"][0]["name_zh"] == "身体"
    checked = validate_psd_roundtrip(str(output), manifest,
                                     layer_files=[layers / "back.png", layers / "front.png"])
    assert checked["valid"] is True


def test_manifest_requires_ascii_psd_identifier_for_unicode_semantic_group(tmp_path):
    layers, manifest_path, manifest = _manifest_layers(tmp_path)
    manifest["groups"][0].pop("psd_name")
    manifest["groups"][0]["id"] = "身体"
    manifest["layers"][0]["group"] = "身体"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = PSDCreator().create_psd(str(layers), str(tmp_path / "invalid.psd"),
                                     manifest_path=str(manifest_path))

    assert result["success"] is False
    assert "psd_name" in result["error"]


def test_unicode_semantic_group_name_can_use_ascii_id_as_psd_name(tmp_path):
    layers, manifest_path, manifest = _manifest_layers(tmp_path)
    manifest["groups"][0].pop("psd_name")
    manifest["groups"][0]["id"] = "body_group"
    manifest["groups"][0]["psd_name"] = "body_group"
    manifest["layers"][0]["group"] = "body_group"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    output = tmp_path / "ascii-named.psd"
    result = PSDCreator().create_psd(str(layers), str(output), manifest_path=str(manifest_path))

    assert result["success"] is True
    assert PSDImage.open(output)[0].name == "body_group"


def test_manifest_crop_origin_is_preserved_without_resampling(tmp_path):
    layers = tmp_path / "layers"
    layers.mkdir()
    cropped = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    cropped.putpixel((1, 0), (20, 40, 60, 255))
    cropped.save(layers / "arm.png")
    manifest = {
        "canvas": {"width": 8, "height": 8},
        "groups": [{"id": "body", "name_zh": "身体", "psd_name": "body", "order": 1}],
        "draw_order": ["arm_R"],
        "layers": [{
            "id": "arm_R", "file": "arm.png", "group": "body",
            "bbox": [6, 2, 6, 2], "origin": [5, 2], "visible": True,
        }],
    }
    manifest_path = tmp_path / "layer_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = PSDCreator().create_psd(
        str(layers), str(tmp_path / "cropped.psd"), manifest_path=str(manifest_path))

    assert result["success"] is True
    psd = PSDImage.open(tmp_path / "cropped.psd")
    layer = next(layer for group in psd if group.is_group() for layer in group)
    rgba = layer.topil().convert("RGBA")
    assert rgba.size == (8, 8)
    assert rgba.getpixel((6, 2)) == (20, 40, 60, 255)
    assert rgba.getpixel((1, 0))[3] == 0
