import numpy as np

from core.qa.layer_reconstruction import verify_reconstruction


def test_verify_reconstruction_reports_zero_error_for_exact_layer():
    source = np.array([[[20, 40, 60, 255], [0, 0, 0, 0]]], dtype=np.uint8)
    layers = {"face": source.copy()}

    result = verify_reconstruction(layers, source)

    assert result["mae"] == 0
    assert result["max_error"] == 0


def test_verify_reconstruction_excludes_synthesized_layers():
    source = np.array([[[20, 40, 60, 255]]], dtype=np.uint8)
    overlay = np.array([[[255, 0, 0, 255]]], dtype=np.uint8)
    layers = {"face": source.copy(), "eyelid_left": {"image": overlay, "synthesized": True}}

    result = verify_reconstruction(layers, source)

    assert result["mae"] == 0
    assert "eyelid_left" in result["excluded_layers"]
