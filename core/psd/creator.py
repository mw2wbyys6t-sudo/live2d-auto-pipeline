#!/usr/bin/env python3
"""
Pygmalion Studio - PSD Creator
Creates layered PSD files from PNG layer directories, with fallback to PNG package.
"""

import json
from pathlib import Path
from typing import Optional, Dict, List, Any

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
        ordered_names: Optional[List[str]] = None,
        manifest_path: Optional[str] = None,
    ) -> Dict:
        """Create a PSD file from a directory of layer PNG files.

        If psd-tools is unavailable, creates a PNG package with composite preview.

        Args:
            layers_dir: Directory containing layer PNG files
            output_path: Output .psd path (default: layers_dir/character.psd)
            canvas_size: (width, height) override; auto-detected if not provided
            layer_order: Explicit list of filenames in back-to-front order
            ordered_names: Semantic layer names in back-to-front order. When
                given, each name maps to ``<name>.png`` inside ``layers_dir``
                and is written into the PSD as the layer name, so the file opens
                in Photoshop/GIMP with meaningful part names
                (``hair_back``/``face``/``eye_L``...) instead of ``layer_000``.

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

        # Collect layer PNGs. An explicit semantic order wins over filename globbing.
        display_names: Optional[List[str]] = None
        layer_files: List[Path] = []
        if ordered_names:
            matched = [(n, layers_path / f"{n}.png") for n in ordered_names]
            matched = [(n, p) for n, p in matched if p.is_file()]
            if matched:
                display_names = [n for n, _ in matched]
                layer_files = [p for _, p in matched]
        manifest = None
        if manifest_path:
            try:
                with open(manifest_path, encoding="utf-8") as handle:
                    manifest = json.load(handle)
                layer_files, display_names = self._manifest_files(layers_path, manifest)
                w = int(manifest["canvas"]["width"])
                h = int(manifest["canvas"]["height"])
                canvas_size = (w, h)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                return {"success": False, "fallback": False,
                        "error": f"Invalid layer manifest: {exc}"}

        if not layer_files:
            layer_files = sorted(layers_path.glob("layer_*.png"))
        if not layer_files:
            # Try all PNG files. 整图预览（preview / composite_preview）不是图层：
            # 它们满画布 100% 不透明，进了 PSD 就是一层盖住所有内容的白板。
            layer_files = sorted([
                f for f in layers_path.glob("*.png")
                if f.stem not in ("preview", "composite_preview")
            ])

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
            return self._create_with_psd_tools(layer_files, output_path, w, h, display_names,
                                               manifest)
        else:
            result = self._create_png_package(layer_files, output_path, w, h, layers_dir)
            result["success"] = False
            result["error"] = result["fallback_reason"]
            return result

    @staticmethod
    def _manifest_files(layers_path: Path, manifest: Dict[str, Any]):
        layers = manifest.get("layers")
        groups = manifest.get("groups")
        if not isinstance(layers, list) or not isinstance(groups, list):
            raise ValueError("manifest requires layers and groups arrays")
        unsupported = {"blend_mode", "clipping", "mask", "smart_object", "effects"}
        for layer in layers:
            features = unsupported.intersection(layer)
            if features:
                raise ValueError(f"unsupported layer feature(s): {', '.join(sorted(features))}")
        group_ids = {group["id"] for group in groups}
        for group in groups:
            psd_name = str(group.get("psd_name") or group.get("id") or "")
            try:
                psd_name.encode("mac_roman")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"unsupported PSD group identifier: {psd_name!r}; "
                    "provide an ASCII psd_name while keeping name_zh in the manifest"
                ) from exc
        draw_order = manifest.get("draw_order", [layer.get("id") for layer in layers])
        by_id = {layer["id"]: layer for layer in layers}
        if len(by_id) != len(layers) or set(draw_order) != set(by_id):
            raise ValueError("manifest layer IDs and draw_order must be unique and complete")
        ordered = [by_id[layer_id] for layer_id in draw_order]
        files = []
        names = []
        for layer in ordered:
            if layer.get("group") not in group_ids:
                raise ValueError(f"unknown group for layer {layer.get('id')!r}")
            path = (layers_path / layer["file"]).resolve()
            if path.parent != layers_path.resolve() or not path.is_file():
                raise ValueError(f"invalid or missing layer file: {layer.get('file')}")
            files.append(path)
            names.append(layer["id"])
        return files, names

    def _create_with_psd_tools(self, layer_files: List[Path], output_path: str, w: int, h: int,
                               display_names: Optional[List[str]] = None,
                               manifest: Optional[Dict[str, Any]] = None) -> Dict:
        """Create actual PSD using psd-tools."""
        try:
            psd = self._PSDImage.new(mode='RGBA', size=(w, h))

            # PSD layer order is back-to-front; frompil appends to its parent.
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            group_layers = {}
            if manifest:
                from psd_tools.api.layers import Group
                for spec in sorted(manifest["groups"], key=lambda item: item.get("order", 0)):
                    group = Group.new(
                        psd,
                        name=spec.get("psd_name") or spec["id"],
                        open_folder=True,
                    )
                    group.visible = bool(spec.get("visible", True))
                    group_layers[spec["id"]] = group
            manifest_by_id = {item["id"]: item for item in manifest["layers"]} if manifest else {}
            for idx, lf in enumerate(layer_files):
                img = Image.open(lf).convert('RGBA')
                layer_name = (display_names[idx]
                              if display_names and idx < len(display_names) else lf.stem)
                if manifest and img.size != (w, h):
                    spec = manifest_by_id[layer_name]
                    origin = spec.get("origin")
                    if not isinstance(origin, (list, tuple)) or len(origin) != 2:
                        raise ValueError(
                            f"cropped layer {lf.name} requires integer canvas origin"
                        )
                    x, y = origin
                    if not isinstance(x, int) or not isinstance(y, int):
                        raise ValueError(f"layer {lf.name} origin must be integer pixels")
                    if x < 0 or y < 0 or x + img.width > w or y + img.height > h:
                        raise ValueError(f"layer {lf.name} crop exceeds canvas bounds")
                    canvas_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    canvas_layer.paste(img, (x, y))
                    img = canvas_layer
                elif not manifest and img.size != (w, h):
                    img = img.resize((w, h), Image.LANCZOS)
                parent = group_layers[manifest_by_id[layer_name]["group"]] if manifest else psd
                layer = self._PixelLayer.frompil(img, parent, name=layer_name)
                if manifest:
                    layer.visible = bool(manifest_by_id[layer_name].get("visible", True))

            psd.save(output_path)
            if manifest:
                from core.psd.validator import validate_psd_roundtrip
                checked = validate_psd_roundtrip(output_path, manifest, layer_files=layer_files)
                if not checked["valid"]:
                    raise ValueError("PSD readback validation failed: " +
                                     "; ".join(checked["errors"]).encode("ascii", "backslashreplace").decode("ascii"))
            log.success(f"PSD created: {output_path}")
            return {
                "success": True,
                "psd_path": output_path,
                "fallback": False,
                "layer_count": len(layer_files),
            }
        except Exception as e:
            message = f"psd-tools creation failed: {e}"
            log.error(message.encode("ascii", "backslashreplace").decode("ascii"))
            return {"success": False, "psd_path": "", "fallback": False,
                    "error": str(e), "layer_count": len(layer_files)}

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
            f.write(f"Pygmalion Studio v0.9.0 - PNG Layer Package\n")
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
            "success": False,
            # 没有真实 .psd 时不要把目录冒充成文件路径：旧实现返回 pkg_dir，
            # 上层据此生成 /output/<目录> 下载链接，点击必然 404。
            "psd_path": "",
            "package_dir": str(pkg_dir),
            "preview_path": str(preview_path),
            "fallback": True,
            "fallback_reason": "psd-tools 未安装：已输出 PNG 图层包，未生成 .psd",
            "layer_count": len(layer_files),
        }


def create_psd_from_layers(layers_dir: str, output_path: Optional[str] = None) -> Dict:
    """Convenience function for PSD creation."""
    creator = PSDCreator()
    return creator.create_psd(layers_dir, output_path)
