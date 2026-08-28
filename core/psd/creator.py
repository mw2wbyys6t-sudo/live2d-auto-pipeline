#!/usr/bin/env python3
"""
Live2D Master Agent - PSD Creator
Creates layered PSD files from PNG layer directories, with fallback to PNG package.
"""

import os
import time
from pathlib import Path
from typing import Optional, Dict, List

from PIL import Image

from core.logger import get_logger
from core.security import validate_path, validate_image_path

log = get_logger("psd")


class PSDCreator:
    """Creates layered PSD files from exported layer PNGs."""

    def __init__(self):
        self._has_psd_tools = False
        try:
            from psd_tools import PSDImage
            from psd_tools.api.layers import PixelLayer
            self._has_psd_tools = True
            self._PSDImage = PSDImage
            self._PixelLayer = PixelLayer
        except ImportError:
            log.warning("psd-tools not installed; will create PNG package instead of PSD")

    def create_psd(
        self,
        layers_dir: str,
        output_path: Optional[str] = None,
        canvas_size: Optional[tuple] = None,
        layer_order: Optional[List[str]] = None,
    ) -> Dict:
        """Create a PSD file from a directory of layer PNG files.

        If psd-tools is unavailable, creates a PNG package with composite preview.

        Args:
            layers_dir: Directory containing layer_XXX.png files
            output_path: Output .psd path (default: layers_dir/character.psd)
            canvas_size: (width, height) override; auto-detected if not provided
            layer_order: Explicit list of filenames in back-to-front order

        Returns:
            Dict with keys: success, psd_path, fallback, layer_count
        """
        # Validate paths
        valid, reason = validate_path(layers_dir)
        if not valid:
            return {"success": False, "error": reason}

        layers_path = Path(layers_dir)
        if not layers_path.is_dir():
            return {"success": False, "error": f"Not a directory: {layers_dir}"}

        if output_path is None:
            output_path = str(layers_path / "character.psd")

        # Collect layer PNGs
        layer_files = sorted(layers_path.glob("layer_*.png"))
        if not layer_files:
            # Try all PNG files
            layer_files = sorted([f for f in layers_path.glob("*.png") if f.name != "preview.png"])

        if not layer_files:
            return {"success": False, "error": f"No layer PNGs found in {layers_dir}"}

        # Order layers (back to front)
        if layer_order:
            ordered = []
            for name in layer_order:
                p = layers_path / name
                if p.exists():
                    ordered.append(p)
            # Add remaining
            ordered += [f for f in layer_files if f not in ordered]
            layer_files = ordered
        else:
            # Already sorted by name which gives back-to-front from layerer
            pass

        # Load first layer to determine canvas size
        first_img = Image.open(layer_files[0]).convert('RGBA')
        w, h = canvas_size or first_img.size

        log.info(f"Creating PSD from {len(layer_files)} layers, canvas {w}x{h}")

        if self._has_psd_tools:
            return self._create_with_psd_tools(layer_files, output_path, w, h)
        else:
            return self._create_png_package(layer_files, output_path, w, h, layers_dir)

    def _create_with_psd_tools(self, layer_files: List[Path], output_path: str, w: int, h: int) -> Dict:
        """Create actual PSD using psd-tools."""
        try:
            psd = self._PSDImage.new(mode='RGBA', size=(w, h))

            # psd-tools adds layers from top; we want first layer = back
            # So reverse to add front layers first
            for i, lf in enumerate(reversed(layer_files)):
                img = Image.open(lf).convert('RGBA')
                if img.size != (w, h):
                    img = img.resize((w, h), Image.LANCZOS)
                layer_name = lf.stem
                layer = self._PixelLayer.frompil(img, psd, name=layer_name)
                psd.append(layer)

            psd.save(output_path)
            log.success(f"PSD created: {output_path}")
            return {
                "success": True,
                "psd_path": output_path,
                "fallback": False,
                "layer_count": len(layer_files),
            }
        except Exception as e:
            log.error(f"psd-tools creation failed: {e}")
            return self._create_png_package(layer_files, output_path, w, h,
                                             Path(output_path).parent)

    def _create_png_package(self, layer_files: List[Path], output_path: str, w: int, h: int,
                             layers_dir: Path) -> Dict:
        """Fallback: create a PNG package (composite + individual layers + guide)."""
        import shutil

        pkg_dir = Path(str(output_path) + "_png_package")
        pkg_dir.mkdir(parents=True, exist_ok=True)

        # Copy layers
        for lf in layer_files:
            shutil.copy2(lf, pkg_dir / lf.name)

        # Create composite preview
        composite = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        for lf in layer_files:
            img = Image.open(lf).convert('RGBA')
            if img.size != (w, h):
                img = img.resize((w, h), Image.LANCZOS)
            composite = Image.alpha_composite(composite, img)

        preview_path = pkg_dir / "composite_preview.png"
        composite.save(preview_path)

        # Write info file
        info_path = pkg_dir / "PACKAGE_INFO.txt"
        with open(info_path, 'w', encoding='utf-8') as f:
            f.write(f"Live2D Master Agent v9.0 - PNG Layer Package\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Canvas size: {w}x{h}\n")
            f.write(f"Layers: {len(layer_files)}\n\n")
            f.write("Note: Install psd-tools for proper PSD output:\n")
            f.write("  pip install psd-tools>=1.9.0\n\n")
            f.write("Layers (back to front):\n")
            for lf in layer_files:
                f.write(f"  {lf.name}\n")

        log.info(f"PNG package created: {pkg_dir} (install psd-tools for PSD output)")
        return {
            "success": True,
            "psd_path": str(pkg_dir),
            "preview_path": str(preview_path),
            "fallback": True,
            "layer_count": len(layer_files),
        }


def create_psd_from_layers(layers_dir: str, output_path: Optional[str] = None) -> Dict:
    """Convenience function for PSD creation."""
    creator = PSDCreator()
    return creator.create_psd(layers_dir, output_path)
