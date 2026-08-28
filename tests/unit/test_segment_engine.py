"""Tests for semantic segmentation engine."""

import tempfile
from pathlib import Path

import pytest
import numpy as np
from PIL import Image

from core.segment_engine.kmeans import KMeansLayerer
from core.segment_engine.semantic import SemanticLayerer
from core.segment_engine.amodal import AmodalCompleter
from core.segment_engine.composer import LayerComposer


def make_test_character(width=256, height=256):
    """Create a simple test character image with distinct color regions."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    arr = np.array(img)

    # Hair (blue, top)
    arr[10:80, 60:196, :] = [100, 100, 255, 255]
    # Face (skin, center)
    arr[60:140, 80:176, :] = [255, 220, 180, 255]
    # Eyes (dark, on face)
    arr[80:95, 90:110, :] = [30, 30, 80, 255]
    arr[80:95, 146:166, :] = [30, 30, 80, 255]
    # Mouth (red)
    arr[115:125, 115:141, :] = [200, 50, 50, 255]
    # Clothes (green, bottom)
    arr[140:240, 50:206, :] = [50, 150, 50, 255]

    return Image.fromarray(arr, "RGBA")


class TestKMeansLayerer:
    """Test K-means clustering layerer."""

    def test_basic_layering(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=5)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            assert result["layer_count"] >= 3
            assert result["layers"]
            assert Path(result["output_dir"]).exists()

    def test_layer_files_exist(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=4)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            for layer in result["layers"]:
                assert Path(layer["path"]).exists()
                with Image.open(layer["path"]) as layer_img:
                    assert layer_img.mode == "RGBA"

    def test_preview_generated(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=4)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            assert Path(result["preview_path"]).exists()

    def test_guide_written(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=4)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            assert Path(result["guide_path"]).exists()
            content = Path(result["guide_path"]).read_text()
            assert "K-Means" in content or "K-means" in content

    def test_empty_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        layerer = KMeansLayerer(k_clusters=5)
        result = layerer.layer(img, output_dir=tempfile.mkdtemp())
        assert result["layer_count"] == 0

    def test_single_color_image(self):
        img = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        layerer = KMeansLayerer(k_clusters=5)
        result = layerer.layer(img, output_dir=tempfile.mkdtemp())
        assert result["layer_count"] >= 1


class TestAmodalCompleter:
    """Test amodal completion / inpainting."""

    def test_cv2_inpainting(self):
        completer = AmodalCompleter()
        img = make_test_character()
        arr = np.array(img)
        mask = np.zeros(arr.shape[:2], dtype=np.uint8)
        mask[80:100, 100:150] = 255
        result = completer.complete(img, mask, mask)
        assert result is not None
        assert result.size == img.size

    def test_simple_fill(self):
        completer = AmodalCompleter()
        img = Image.new("RGB", (64, 64), color=(200, 100, 50))
        mask = np.zeros((64, 64), dtype=np.uint8)
        mask[20:40, 20:40] = 255
        result = completer._simple_fill(np.array(img), mask)
        assert result.shape == (64, 64, 3)


class TestLayerComposer:
    """Test layer composition and ordering."""

    def test_standard_order_exists(self):
        assert len(LayerComposer.STANDARD_LAYER_ORDER) == 18
        assert "face_base" in LayerComposer.STANDARD_LAYER_ORDER
        assert "hair_front" in LayerComposer.STANDARD_LAYER_ORDER
        assert "hair_back" in LayerComposer.STANDARD_LAYER_ORDER

    def test_compose_basic(self):
        composer = LayerComposer()
        img = make_test_character()
        arr = np.array(img)
        # Use non-overlapping masks for clean extraction
        h, w = arr.shape[:2]
        hair_mask = np.zeros((h, w), dtype=bool)
        hair_mask[10:80, 60:196] = True
        face_mask = np.zeros((h, w), dtype=bool)
        face_mask[60:140, 80:176] = True
        clothes_mask = np.zeros((h, w), dtype=bool)
        clothes_mask[140:240, 50:206] = True

        part_masks = {
            "hair_back": hair_mask,
            "face_base": face_mask,
            "clothes_top": clothes_mask,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = composer.compose(part_masks, img, tmpdir)
            # compose() returns a dict keyed by part name with metadata
            assert "hair_back" in result
            assert "face_base" in result
            assert "clothes_top" in result
            for name, info in result.items():
                assert "path" in info
                assert Path(info["path"]).exists()

    def test_generate_occlusion_map(self):
        composer = LayerComposer()
        masks = {
            "hair_front": np.zeros((64, 64), dtype=bool),
            "face_base": np.zeros((64, 64), dtype=bool),
        }
        masks["hair_front"][10:30, :] = True
        masks["face_base"][20:50, :] = True
        occ = composer.generate_occlusion_map(masks)
        assert isinstance(occ, dict)
        assert "hair_front" in occ
        assert "face_base" in occ

    def test_reorder_layers(self):
        composer = LayerComposer()
        # Create metadata dicts in arbitrary order
        layers = {
            "eyes": {"name": "eyes"},
            "hair_back": {"name": "hair_back"},
            "face_base": {"name": "face_base"},
            "hair_front": {"name": "hair_front"},
        }
        reordered = composer.reorder_layers(layers)
        keys = list(reordered.keys())
        # Standard order: hair_back (index 1) comes before face_base (index 7)
        # face_base (index 7) comes before hair_front (index 3)... wait no
        # STANDARD_LAYER_ORDER: scalp(0), hair_back(1), hair_mid(2), hair_front(3),
        #   eyebrows(4), eyes(5), nose_mouth(6), face_base(7)...
        # So hair_back < hair_front < eyes < face_base
        assert keys.index("hair_back") < keys.index("hair_front")
        assert keys.index("hair_back") < keys.index("eyes")

    def test_generate_layer_json(self):
        composer = LayerComposer()
        layers = {
            "hair_back": {"name": "hair_back", "path": "/tmp/a.png", "bbox": (0,0,10,10),
                         "size": (10,10), "pixel_count": 100, "mean_color": (100,100,255),
                         "occluded_by": [], "completed": False},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = composer.generate_layer_json(layers, str(Path(tmpdir) / "layers.json"))
            assert Path(path).exists()
            import json
            data = json.loads(Path(path).read_text())
            assert "layers" in data
            assert data["layer_count"] == 1
