"""Tests for face tracking BlendShape mapping."""

import pytest
from drivers.face_tracker.blendshape_mapper import BlendShapeMapper


class TestBlendShapeMapper:
    """Test ARKit → Live2D parameter mapping."""

    def setup_method(self):
        self.mapper = BlendShapeMapper(smoothing_factor=0.5)

    def test_eye_blink_mapping(self):
        """Eye blink blendshapes should map to ParamEyeLOpen/ParamEyeROpen."""
        blendshapes = {
            "eyeBlinkLeft": 1.0,
            "eyeBlinkRight": 1.0,
        }
        params = self.mapper.map_to_live2d_params(blendshapes)
        assert "ParamEyeLOpen" in params
        assert "ParamEyeROpen" in params
        # Blink = 1.0 should map to eye open = 0.0 (closed)
        assert params["ParamEyeLOpen"] < 0.1
        assert params["ParamEyeROpen"] < 0.1

    def test_eyes_open(self):
        """When not blinking, eyes should be open."""
        blendshapes = {
            "eyeBlinkLeft": 0.0,
            "eyeBlinkRight": 0.0,
        }
        params = self.mapper.map_to_live2d_params(blendshapes)
        assert params["ParamEyeLOpen"] > 0.9
        assert params["ParamEyeROpen"] > 0.9

    def test_mouth_open(self):
        """Jaw open should map to ParamMouthOpenY."""
        blendshapes = {"jawOpen": 0.8}
        params = self.mapper.map_to_live2d_params(blendshapes)
        assert "ParamMouthOpenY" in params
        assert params["ParamMouthOpenY"] > 0.5

    def test_mouth_closed(self):
        blendshapes = {"jawOpen": 0.0}
        params = self.mapper.map_to_live2d_params(blendshapes)
        assert params["ParamMouthOpenY"] < 0.1

    def test_smile_mapping(self):
        """Smile should map to ParamMouthForm."""
        blendshapes = {
            "mouthSmileLeft": 1.0,
            "mouthSmileRight": 1.0,
        }
        params = self.mapper.map_to_live2d_params(blendshapes)
        assert "ParamMouthForm" in params

    def test_brow_raise(self):
        blendshapes = {"browInnerUp": 0.7, "browOuterUpLeft": 0.5, "browOuterUpRight": 0.5}
        params = self.mapper.map_to_live2d_params(blendshapes)
        assert "ParamBrowLY" in params
        assert "ParamBrowRY" in params

    def test_smoothing(self):
        """Smoothing should reduce sudden changes."""
        prev = {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}
        curr = {"ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0}
        smoothed = self.mapper.smooth_parameters(curr, prev)
        # With smoothing_factor=0.5: 1.0*0.5 + 0.0*0.5 = 0.5
        assert 0.0 < smoothed["ParamEyeLOpen"] < 1.0

    def test_deadzone(self):
        """Values within deadzone of their default are reset to default."""
        # ParamMouthForm default is 0.0; a tiny value 0.005 should be zeroed
        params = {"ParamMouthForm": 0.005, "ParamMouthOpenY": 0.5}
        filtered = self.mapper.apply_deadzone(params, deadzone=0.02)
        # 0.005 is within deadzone of 0.0 default → reset to 0.0
        assert filtered["ParamMouthForm"] == 0.0
        # 0.5 is far from 0.0 → unchanged
        assert filtered["ParamMouthOpenY"] == 0.5

    def test_parameter_ranges(self):
        """All output params should have valid ranges."""
        ranges = self.mapper.get_parameter_ranges()
        assert "ParamAngleX" in ranges
        assert "ParamEyeLOpen" in ranges
        assert "ParamMouthOpenY" in ranges
        for name, rng in ranges.items():
            # ranges are (min, max, default) tuples
            assert len(rng) == 3
            lo, hi, default = rng
            assert lo < hi
            assert lo <= default <= hi

    def test_empty_blendshapes(self):
        """Empty input should return default parameters."""
        params = self.mapper.map_to_live2d_params({})
        # Should return defaults (eyes open, mouth closed)
        assert "ParamEyeLOpen" in params
        assert params["ParamEyeLOpen"] >= 0.9
