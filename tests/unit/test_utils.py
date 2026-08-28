"""Tests for core utility modules."""

import os
import tempfile
import time
from pathlib import Path

import pytest
import numpy as np
from PIL import Image

from core.utils.image_utils import (
    load_image, save_image, resize_to_max, remove_background,
    enhance_for_layering, composite_layers, create_preview, alpha_to_mask,
)
from core.utils.file_utils import (
    ensure_dir, safe_filename, get_timestamp, get_file_size_mb,
    hash_file, find_images, cleanup_old_files,
)


class TestImageUtils:
    """Test image utility functions."""

    def test_load_and_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
            path = os.path.join(tmpdir, "test.png")
            save_image(img, path)
            loaded = load_image(path)
            assert loaded.size == (64, 64)
            assert loaded.mode == "RGBA"

    def test_resize_to_max(self):
        img = Image.new("RGB", (2048, 1024), (100, 100, 100))
        resized = resize_to_max(img, max_dim=512)
        assert max(resized.size) <= 512

    def test_resize_no_upscale(self):
        img = Image.new("RGB", (100, 100), (100, 100, 100))
        resized = resize_to_max(img, max_dim=512)
        assert resized.size == (100, 100)  # no upscaling

    def test_alpha_to_mask(self):
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        arr = np.array(img)
        arr[3:7, 3:7, 3] = 255
        img = Image.fromarray(arr, "RGBA")
        mask = alpha_to_mask(img)
        assert mask.shape == (10, 10)
        assert mask[5, 5] == True
        assert mask[0, 0] == False

    def test_composite_layers(self):
        bottom = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        top = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        arr_b = np.array(bottom)
        arr_b[:, :32] = [255, 0, 0, 255]
        arr_t = np.array(top)
        arr_t[:, 32:] = [0, 255, 0, 255]
        bottom = Image.fromarray(arr_b)
        top = Image.fromarray(arr_t)
        result = composite_layers([bottom, top])
        assert result.size == (64, 64)
        assert result.mode == "RGBA"

    def test_enhance_for_layering(self):
        img = Image.new("RGB", (100, 100), (128, 128, 128))
        result = enhance_for_layering(img)
        assert result is not None
        assert result.size == (100, 100)

    def test_create_preview(self):
        layers = [
            Image.new("RGBA", (50, 50), (255, 0, 0, 128)),
            Image.new("RGBA", (50, 50), (0, 255, 0, 128)),
        ]
        preview = create_preview(layers, labels=["red", "green"])
        assert preview is not None
        assert preview.size[0] > 50  # grid should be wider


class TestFileUtils:
    """Test file utility functions."""

    def test_ensure_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a", "b", "c")
            result = ensure_dir(path)
            assert result.exists()
            assert result.is_dir()

    def test_safe_filename(self):
        assert safe_filename("hello world") == "hello_world"
        # basename strips path components, leaving just "passwd"
        assert safe_filename("../../etc/passwd") == "passwd"
        # Illegal chars become underscores; '*' collapses with preceding '_'
        assert safe_filename("file<>:\"|?*.png") == "file_.png"
        assert safe_filename("日本語.png") == "日本語.png"

    def test_get_timestamp(self):
        ts = get_timestamp()
        assert isinstance(ts, str)
        assert len(ts) > 0

    def test_get_file_size_mb(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"x" * (1024 * 1024))
            path = f.name
        try:
            size_mb = get_file_size_mb(path)
            assert 0.9 < size_mb < 1.1
        finally:
            os.unlink(path)

    def test_hash_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hello world")
            path = f.name
        try:
            h = hash_file(path)
            assert len(h) == 64  # SHA256 hex
            assert h == hash_file(path)  # deterministic
        finally:
            os.unlink(path)

    def test_find_images(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Image.new("RGB", (10, 10), (0, 0, 0)).save(os.path.join(tmpdir, "a.png"))
            Image.new("RGB", (10, 10), (0, 0, 0)).save(os.path.join(tmpdir, "b.jpg"))
            Path(os.path.join(tmpdir, "notimage.txt")).write_text("hello")
            images = find_images(tmpdir)
            assert len(images) == 2

    def test_cleanup_old_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_file = os.path.join(tmpdir, "old.txt")
            Path(old_file).write_text("old")
            old_time = time.time() - (10 * 86400)
            os.utime(old_file, (old_time, old_time))
            new_file = os.path.join(tmpdir, "new.txt")
            Path(new_file).write_text("new")

            removed = cleanup_old_files(tmpdir, max_age_days=7)
            assert removed == 1
            assert not os.path.exists(old_file)
            assert os.path.exists(new_file)


class TestSecurity:
    """Test security functions."""

    def test_sanitize_prompt(self):
        from core.security import sanitize_prompt
        result = sanitize_prompt("hello world; rm -rf /")
        assert "rm" not in result.lower() or ";" not in result

    def test_validate_image_path(self):
        from core.security import validate_image_path
        with tempfile.TemporaryDirectory() as tmpdir:
            img_path = os.path.join(tmpdir, "test.png")
            Image.new("RGB", (10, 10), (0, 0, 0)).save(img_path)
            valid, _ = validate_image_path(img_path)
            assert valid
        valid, _ = validate_image_path("/nonexistent/file.png")
        assert not valid

    def test_sanitize_filename(self):
        from core.security import sanitize_filename
        result = sanitize_filename("../../../etc/passwd")
        # Security properties: no traversal, no separators
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result
        # basename extraction leaves "passwd"
        assert "passwd" in result
