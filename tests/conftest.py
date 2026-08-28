"""Pytest configuration and shared fixtures for Live2D Master Agent tests."""

import sys
import os
import tempfile
from pathlib import Path

import pytest
import numpy as np
from PIL import Image

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def test_image():
    """Create a simple RGBA test image."""
    img = Image.new("RGBA", (128, 128), (100, 150, 200, 255))
    return img


@pytest.fixture
def test_character_image():
    """Create a test character with distinct colored regions."""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    arr = np.array(img)
    # Hair (blue top)
    arr[10:80, 60:196] = [100, 100, 255, 255]
    # Face (skin center)
    arr[60:140, 80:176] = [255, 220, 180, 255]
    # Eyes
    arr[80:95, 90:110] = [30, 30, 80, 255]
    arr[80:95, 146:166] = [30, 30, 80, 255]
    # Clothes (green bottom)
    arr[140:240, 50:206] = [50, 150, 50, 255]
    return Image.fromarray(arr, "RGBA")


@pytest.fixture
def temp_dir():
    """Provide a temporary directory that's cleaned up after test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_output(temp_dir):
    """Provide an output directory path."""
    out = temp_dir / "output"
    out.mkdir()
    return str(out)
