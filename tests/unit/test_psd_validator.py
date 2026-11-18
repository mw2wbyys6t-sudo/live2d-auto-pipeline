from PIL import Image

from core.psd.creator import PSDCreator
from core.psd.validator import read_psd_layer_order, validate_psd_roundtrip
from core.segment_engine.composer import LayerComposer


def test_roundtrip_validator_reports_group_layer_mismatch(tmp_path):
    layers = tmp_path / "layers"
    layers.mkdir()
    Image.new("RGBA", (6, 6), (20, 30, 40, 255)).save(layers / "face.png")
    result = PSDCreator().create_psd(str(layers), str(tmp_path / "face.psd"),
                                     ordered_names=["face"])
    assert result["success"] is True

    manifest = {
        "canvas": {"width": 6, "height": 6},
        "groups": [{"id": "head_face", "name_zh": "Face", "visible": True, "order": 1}],
        "draw_order": ["face"],
        "layers": [{"id": "face", "file": "face.png", "group": "head_face",
                    "bbox": [0, 0, 6, 6], "visible": True}],
    }

    checked = validate_psd_roundtrip(result["psd_path"], manifest)

    assert checked["valid"] is False
    assert any("group" in error.lower() for error in checked["errors"])


def test_roundtrip_validator_rejects_missing_or_invalid_psd(tmp_path):
    checked = validate_psd_roundtrip(str(tmp_path / "absent.psd"), {})
    assert checked["valid"] is False
    assert checked["errors"]


def test_psd_order_reader_flattens_manifest_groups_in_draw_order(tmp_path):
    layers = tmp_path / "layers"
    layers.mkdir()
    for name in ("hair_back", "face", "eyebrows", "mouth"):
        Image.new("RGBA", (8, 8), (20, 30, 40, 255)).save(layers / f"{name}.png")
    manifest_path = layers / "manifest.json"
    manifest_path.write_text(
        __import__("json").dumps({
            "canvas": {"width": 8, "height": 8},
            "groups": [
                {"id": "head_hair", "psd_name": "01_head_hair", "name_zh": "头发", "order": 1},
                {"id": "head_face", "psd_name": "02_head_face", "name_zh": "面部", "order": 2},
            ],
            "draw_order": ["hair_back", "face", "eyebrows", "mouth"],
            "layers": [
                {"id": "hair_back", "file": "hair_back.png", "group": "head_hair", "bbox": [0, 0, 7, 7]},
                {"id": "face", "file": "face.png", "group": "head_face", "bbox": [0, 0, 7, 7]},
                {"id": "eyebrows", "file": "eyebrows.png", "group": "head_face", "bbox": [0, 0, 7, 7]},
                {"id": "mouth", "file": "mouth.png", "group": "head_face", "bbox": [0, 0, 7, 7]},
            ],
        }), encoding="utf-8")
    result = PSDCreator().create_psd(
        str(layers), str(tmp_path / "grouped.psd"), manifest_path=str(manifest_path))
    assert result["success"] is True

    actual = read_psd_layer_order(result["psd_path"])
    expected = [name for name in LayerComposer.STANDARD_LAYER_ORDER
                if name in {"hair_back", "face", "eyebrows", "mouth"}]
    assert actual == expected
