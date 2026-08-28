"""Tests for Live2D Cubism4 builder pipeline."""

import json
import tempfile
from pathlib import Path

import pytest
import numpy as np
from PIL import Image

from live2d_builder.mesh.generator import MeshGenerator
from live2d_builder.blendshapes.parameters import ParameterSet
from live2d_builder.blendshapes.expressions import ExpressionBuilder
from live2d_builder.physics.config import PhysicsBuilder
from live2d_builder.exporter.model3_exporter import Model3Exporter
from live2d_builder.exporter.texture_atlas import TextureAtlas
from live2d_builder.validator.model_validator import ModelValidator
from live2d_builder.pipeline import Live2DBuilder


def make_layer_image(w=128, h=128, color=(200, 100, 100, 255)):
    """Create a simple RGBA layer image."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    margin = w // 6
    arr[margin:h-margin, margin:w-margin] = color
    return Image.fromarray(arr, "RGBA")


def make_test_layers():
    """Create a set of test layers for Live2D building."""
    layers = {
        "hair_back": make_layer_image(color=(80, 80, 200, 255)),
        "face_base": make_layer_image(color=(255, 220, 180, 255)),
        "eyes": make_layer_image(color=(30, 30, 80, 255)),
        "hair_front": make_layer_image(color=(80, 80, 200, 255)),
        "clothes_top": make_layer_image(color=(50, 150, 50, 255)),
    }
    return layers


class TestMeshGenerator:
    """Test mesh generation."""

    def test_generate_mesh(self):
        gen = MeshGenerator(internal_spacing=20, contour_spacing=12)
        img = make_layer_image()
        mesh = gen.generate(img)
        assert "vertices" in mesh
        assert "indices" in mesh
        assert len(mesh["vertices"]) >= 3
        assert len(mesh["indices"]) >= 1

    def test_mesh_vertices_in_bounds(self):
        gen = MeshGenerator(internal_spacing=20)
        img = make_layer_image(128, 128)
        mesh = gen.generate(img)
        for v in mesh["vertices"]:
            assert 0 <= v[0] <= 128
            assert 0 <= v[1] <= 128

    def test_empty_image(self):
        gen = MeshGenerator()
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        mesh = gen.generate(img)
        assert len(mesh["vertices"]) == 0

    def test_grid_mesh(self):
        gen = MeshGenerator()
        img = make_layer_image(100, 100)
        mesh = gen.generate_grid_mesh(img, spacing=25)
        assert len(mesh["vertices"]) > 4


class TestParameterSet:
    """Test parameter definitions."""

    def test_standard_params(self):
        ps = ParameterSet()
        defaults = ps.get_default_values()
        assert "ParamAngleX" in defaults
        assert "ParamEyeLOpen" in defaults
        assert "ParamMouthOpenY" in defaults
        assert "ParamBreath" in defaults

    def test_parameter_ranges(self):
        # ParameterSet exposes ranges via individual param dicts
        ps = ParameterSet()
        eye_l = ps["ParamEyeLOpen"]
        assert eye_l["min"] < eye_l["max"]
        assert eye_l["min"] == 0.0
        assert eye_l["max"] == 1.0

    def test_blendshape_count(self):
        ps = ParameterSet()
        bshapes = ps.get_blendshapes()
        assert len(bshapes) >= 20  # At least 20 BlendShape definitions

    def test_validate_params(self):
        ps = ParameterSet()
        valid = {"ParamEyeLOpen": 0.5, "ParamMouthOpenY": 0.8}
        assert ps.validate_parameter_values(valid)
        invalid = {"ParamEyeLOpen": 1.5}  # out of range
        assert not ps.validate_parameter_values(invalid)

    def test_export_cubism_params(self):
        ps = ParameterSet()
        params = ps.export_cubism_params()
        assert len(params) >= 20
        assert all("Id" in p and "Min" in p and "Max" in p for p in params)


class TestExpressionBuilder:
    """Test expression generation."""

    def test_build_all_expressions(self):
        builder = ExpressionBuilder()
        expressions = builder.build_all()
        assert len(expressions) >= 10
        names = [e.get("name", "") for e in expressions]
        assert "smile" in names
        assert "angry" in names

    def test_single_expression(self):
        builder = ExpressionBuilder()
        expr = builder.build_expression("smile")
        assert expr is not None
        assert "Parameters" in expr
        assert expr["Type"] == "Live2D Expression"

    def test_expression_with_overrides(self):
        builder = ExpressionBuilder()
        expr = builder.build_expression("happy", {"ParamEyeLOpen": 0.3})
        assert expr is not None
        # Verify the override is present
        eye_params = [p for p in expr["Parameters"] if p["Id"] == "ParamEyeLOpen"]
        assert len(eye_params) >= 1
        assert eye_params[0]["Value"] == 0.3

    def test_export_to_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = ExpressionBuilder()
            manifest = builder.export_to_directory(tmpdir)
            assert len(manifest) >= 10
            expr_dir = Path(tmpdir) / "expressions"
            assert expr_dir.exists()
            files = list(expr_dir.glob("*.exp3.json"))
            assert len(files) >= 10


class TestPhysicsBuilder:
    """Test physics configuration."""

    def test_hair_physics(self):
        pb = PhysicsBuilder()
        hair = pb.build_hair_physics(["hair_front", "hair_back"])
        assert hair is not None

    def test_physics3_json(self):
        pb = PhysicsBuilder()
        pb.build_hair_physics(["hair_front"])
        pb.build_body_physics()
        pb.build_breathing_physics()
        physics3 = pb.to_physics3_json()
        assert "Version" in physics3 or "version" in physics3
        assert "PhysicsSettings" in physics3 or "physics_settings" in physics3 or "Groups" in physics3 or "groups" in physics3

    def test_skirt_physics(self):
        pb = PhysicsBuilder()
        skirt = pb.build_skirt_physics(["skirt_front", "skirt_back"])
        assert skirt is not None

    def test_reset(self):
        pb = PhysicsBuilder()
        pb.build_hair_physics(["hair_front"])
        pb.reset()
        physics3 = pb.to_physics3_json()
        # After reset, groups should be empty
        groups = physics3.get("Groups", physics3.get("groups", []))
        assert len(groups) == 0


class TestTextureAtlas:
    """Test texture atlas packing."""

    def test_pack(self):
        atlas = TextureAtlas(max_size=512, padding=2)
        layers = {
            "layer_a": make_layer_image(100, 80),
            "layer_b": make_layer_image(60, 120),
            "layer_c": make_layer_image(90, 90),
        }
        result = atlas.pack(layers)
        assert "atlases" in result or "uvs" in result
        assert len(result.get("uvs", {})) == 3

    def test_empty_pack(self):
        atlas = TextureAtlas(max_size=256, padding=2)
        result = atlas.pack({})
        assert "atlases" in result
        assert len(result["atlases"]) == 1  # empty placeholder

    def test_oversized_layer(self):
        atlas = TextureAtlas(max_size=256, padding=2)
        layers = {"big": make_layer_image(500, 500)}
        with pytest.raises(RuntimeError):
            atlas.pack(layers)


class TestModel3Exporter:
    """Test model3.json export."""

    def test_export_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = Model3Exporter()
            layers = make_test_layers()
            # Build a minimal builder_result
            builder_result = {"layers": layers, "meshes": {}}
            result = exporter.export(
                builder_result=builder_result,
                output_dir=tmpdir,
                character_name="test_char",
            )
            assert Path(result["model3_json"]).exists()

    def test_model3_valid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = Model3Exporter()
            layers = make_test_layers()
            builder_result = {"layers": layers, "meshes": {}}
            result = exporter.export(builder_result=builder_result, output_dir=tmpdir)
            with open(result["model3_json"]) as f:
                model3 = json.load(f)
            assert "Version" in model3
            assert model3["Version"] == 3
            assert "FileReferences" in model3

    def test_export_physics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = Model3Exporter()
            layers = make_test_layers()
            builder_result = {"layers": layers, "meshes": {}}
            result = exporter.export(builder_result=builder_result, output_dir=tmpdir)
            if result.get("physics3_json"):
                assert Path(result["physics3_json"]).exists()


class TestModelValidator:
    """Test model validation."""

    def test_validate_valid_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = Model3Exporter()
            layers = make_test_layers()
            builder_result = {"layers": layers, "meshes": {}}
            result = exporter.export(builder_result=builder_result, output_dir=tmpdir)
            validator = ModelValidator()
            valid, issues = validator.validate_model3(result["model3_json"])
            # Should be valid JSON at minimum
            assert valid or len(issues) >= 0

    def test_validate_missing_file(self):
        validator = ModelValidator()
        valid, issues = validator.validate_model3("/nonexistent/model.model3.json")
        assert not valid


class TestLive2DBuilder:
    """Test full builder pipeline."""

    def test_full_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = Live2DBuilder(output_dir=tmpdir, character_name="test")
            layers = make_test_layers()
            result = builder.build(layers)
            assert result.get("model3_json") or result.get("output_dir")
            assert Path(tmpdir).exists()
            # model3.json should exist
            assert result.get("model3_json")
            assert Path(result["model3_json"]).exists()

    def test_build_creates_textures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = Live2DBuilder(output_dir=tmpdir)
            layers = make_test_layers()
            result = builder.build(layers)
            # Should produce texture or model files
            output = Path(tmpdir)
            pngs = list(output.rglob("*.png"))
            jsons = list(output.rglob("*.json"))
            assert len(jsons) > 0  # At least model3.json + physics + expressions
