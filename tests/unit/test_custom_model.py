from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.custom_model import (
    discover,
    find_task,
    load_manifest,
    make_manifest_dict,
    parse_manifest,
    postprocess_depth,
    postprocess_segmentation,
    preprocess,
    resolve_roots,
    validate_manifest,
)


def _depth_manifest(name: str = "my-depth") -> dict:
    return {
        "schema_version": "1.0",
        "name": name,
        "task": "depth",
        "format": "onnx",
        "file": "model.onnx",
        "input": {
            "name": "image",
            "shape": [1, 3, 64, 64],
            "color_order": "RGB",
            "scale": 0.00392156862745098,
            "mean": [0.0, 0.0, 0.0],
            "std": [1.0, 1.0, 1.0],
        },
        "output": {
            "name": "depth",
            "kind": "depth",
            "depth_range": [0.0, 1.0],
            "near_is_large": True,
        },
    }


def _write_model(root: Path, data: dict) -> Path:
    directory = root / data["name"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (directory / "model.onnx").write_bytes(b"placeholder")
    return directory


def test_valid_depth_manifest_parses() -> None:
    manifest = parse_manifest(_depth_manifest())

    assert manifest.task == "depth"
    assert manifest.input.shape == (1, 3, 64, 64)
    assert manifest.output.kind == "depth"
    assert validate_manifest(_depth_manifest()) == []


def test_invalid_manifests_are_rejected() -> None:
    bad_task = _depth_manifest()
    bad_task["task"] = "generation"
    assert any("task" in e for e in validate_manifest(bad_task))

    bad_shape = _depth_manifest()
    bad_shape["input"]["shape"] = [1, 4, 64, 64]
    assert any("通道数" in e for e in validate_manifest(bad_shape))

    bad_order = _depth_manifest()
    bad_order["input"]["color_order"] = "RGBA"
    assert any("color_order" in e for e in validate_manifest(bad_order))

    bad_labels = _depth_manifest()
    bad_labels["task"] = "segmentation"
    bad_labels["output"] = {"kind": "label_map"}
    assert any("labels" in e for e in validate_manifest(bad_labels))


def test_load_manifest_requires_weight_file(tmp_path: Path) -> None:
    data = _depth_manifest()
    directory = tmp_path / data["name"]
    directory.mkdir()
    (directory / "model.json").write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        load_manifest(str(directory))


def test_discover_and_find_task(tmp_path: Path) -> None:
    _write_model(tmp_path, _depth_manifest("depth-a"))
    invalid = _depth_manifest("broken")
    invalid["task"] = "nope"
    _write_model(tmp_path, invalid)

    found = discover([str(tmp_path)])
    assert [m.name for m in found] == ["depth-a"]
    assert find_task([str(tmp_path)], "depth").name == "depth-a"
    assert find_task([str(tmp_path)], "segmentation") is None


def test_resolve_roots_prefers_env_and_base(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LIVE2D_CUSTOM_MODELS", str(tmp_path / "env-models"))
    roots = resolve_roots(base_dir=str(tmp_path / "app"))

    assert roots[0].endswith("env-models")
    assert str(tmp_path / "app" / "models" / "custom") in roots


def test_make_manifest_dict_for_depth_and_segmentation() -> None:
    depth = make_manifest_dict("d", "depth", shape=(1, 3, 256, 256))
    assert validate_manifest(depth) == []

    parts = make_manifest_dict(
        "s",
        "segmentation",
        shape=(1, 3, 1024, 1024),
        labels={"0": "background", "1": "hair_back", "2": "face_base"},
    )
    assert validate_manifest(parts) == []
    assert parts["output"]["labels"]["2"] == "face_base"


def test_make_manifest_dict_rejects_missing_labels() -> None:
    with pytest.raises(ValueError):
        make_manifest_dict("s", "segmentation", output_kind="label_map")


def test_preprocess_builds_nchw_tensor() -> None:
    manifest = parse_manifest(_depth_manifest())
    image = Image.new("RGB", (100, 50), (255, 128, 0))

    tensor = preprocess(image, manifest.input)

    assert tensor.shape == (1, 3, 64, 64)
    assert tensor.dtype == np.float32
    assert np.isclose(tensor[0, 0].mean(), 1.0, atol=1e-5)


def test_postprocess_depth_normalizes_and_resizes() -> None:
    output = np.array([[10.0, 20.0], [30.0, 40.0]], dtype=np.float32)
    spec = parse_manifest(_depth_manifest()).output
    spec.depth_range = (10.0, 40.0)

    depth = postprocess_depth(output, spec, (100, 80))

    assert depth.shape == (80, 100)
    assert float(depth.min()) == pytest.approx(0.0, abs=1e-5)
    assert float(depth.max()) == pytest.approx(1.0, abs=1e-5)


def test_postprocess_depth_respects_near_is_small() -> None:
    output = np.array([[0.0, 1.0]], dtype=np.float32)
    spec = parse_manifest(_depth_manifest()).output
    spec.near_is_large = False

    depth = postprocess_depth(output, spec, (2, 1))

    assert depth[0, 0] == pytest.approx(1.0)
    assert depth[0, 1] == pytest.approx(0.0)


def test_postprocess_label_map_to_masks() -> None:
    output = np.array([[0, 1], [1, 0]], dtype=np.int64)
    spec = parse_manifest(_depth_manifest()).output
    spec.kind = "label_map"
    spec.labels = {"0": "background", "1": "hair_back"}

    masks = postprocess_segmentation(output, spec, (2, 2))

    assert set(masks) == {"background", "hair_back"}
    assert masks["hair_back"].sum() == 2
    assert not (masks["background"] & masks["hair_back"]).any()


def test_postprocess_mask_stack() -> None:
    output = np.zeros((1, 2, 2, 2), dtype=np.float32)
    output[0, 0, 0, 0] = 1.0
    output[0, 1, 1, 1] = 1.0
    spec = parse_manifest(_depth_manifest()).output
    spec.kind = "masks"
    spec.labels = {"0": "eye_white", "1": "eye_iris"}

    masks = postprocess_segmentation(output, spec, (2, 2))

    assert masks["eye_white"][0, 0]
    assert masks["eye_iris"][1, 1]
