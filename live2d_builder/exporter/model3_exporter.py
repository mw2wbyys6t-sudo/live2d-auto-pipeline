#!/usr/bin/env python3
"""Export complete Live2D Cubism 4 model3.json and companion files.

Generates:
- <name>.model3.json  (Cubism 4 model definition)
- <name>.texture_NN.png (texture atlases)
- <name>.physics3.json
- expressions/*.exp3.json (28 expressions)
- Cubism Editor import guide (markdown)
- Zip package ready for distribution

Note: .moc3 binary files are NOT generated — they are a proprietary
Cubism Editor output. The model3.json references a placeholder moc3
path that users produce by importing the PSD and mesh data into the
Cubism Editor.
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from core.logger import get_logger
from live2d_builder.exporter.texture_atlas import TextureAtlas
from live2d_builder.blendshapes.parameters import ParameterSet
from live2d_builder.blendshapes.expressions import ExpressionBuilder
from live2d_builder.physics.config import PhysicsBuilder

log = get_logger("exporter.model3")


class Model3Exporter:
    """Export a complete Cubism 4 model package."""

    def __init__(self, max_atlas_size: int = 2048) -> None:
        self.max_atlas_size = max_atlas_size
        self._atlas = TextureAtlas(max_size=max_atlas_size)
        self._params = ParameterSet()
        self._expressions = ExpressionBuilder()
        self._physics = PhysicsBuilder()

    # ------------------------------------------------------------------
    # Main export
    # ------------------------------------------------------------------

    def export(
        self,
        builder_result: Dict[str, Any],
        output_dir: str,
        character_name: str = "character",
        compress_webp: bool = False,
        webp_quality: int = 85,
    ) -> Dict[str, str]:
        """Export all model files to ``output_dir``.

        Args:
            builder_result:  Output of :class:`Live2DBuilder.build`.
            output_dir:      Directory to write files into.
            character_name:  Base filename for the model.
            compress_webp:   导出纹理时转换为 WebP（体积缩小 30%~50%）。
                             Cubism 4/5 SDK 均支持 WebP 纹理加载。
            webp_quality:    WebP 质量，0~100，默认 85。

        Returns:
            dict with paths to all generated files.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        layers: Dict[str, Image.Image] = builder_result.get("layers", {})
        meshes: Dict[str, Dict] = builder_result.get("meshes", {})
        physics_data: Dict = builder_result.get("physics3")
        if physics_data is None:
            physics_data = self._build_default_physics(layers)

        # 1. Pack textures（支持 WebP 压缩导出优化）
        texture_files = self._export_textures(
            layers, out, character_name,
            compress_webp=compress_webp, webp_quality=webp_quality,
        )

        # 2. Build file references
        moc_filename = f"{character_name}.moc3"
        physics_filename = f"{character_name}.physics3.json"

        # 3. Expressions
        expr_manifest = self._expressions.export_to_directory(str(out))

        # 4. Physics
        physics_path = out / physics_filename
        physics_path.write_text(
            json.dumps(physics_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 5. Groups (EyeBlink, LipSync)
        groups = self._build_groups(builder_result)

        # 6. Hit areas
        hit_areas = self._build_hit_areas(layers)

        # 7. Layout
        layout = self._build_layout()

        # 7b. Deformers (warp grids + eye rotation pivots). The eye rotation
        # deformers carry the *measured* eyeball centroid as their pivot so
        # the gaze rotation centre is inspectable from model3.json.
        deformers = self._build_deformers(builder_result)

        # 7c. Per-layer preview textures for the browser player. The packed
        # atlases above are bin-packed crops (correct for Cubism, wrong for a
        # single-quad preview), so we also export one full-canvas PNG per
        # layer plus a manifest. The web player stacks these into a coherent
        # character and can deform each part independently.
        preview_layers = self._export_preview_layers(layers, out, character_name)

        # 8. Assemble model3.json
        file_refs = self._build_file_references(
            moc=moc_filename,
            textures=texture_files,
            physics=physics_filename,
            expressions=expr_manifest,
        )
        param_list = self._params.export_cubism_params()

        model3: Dict[str, Any] = {
            "Version": 3,
            "FileReferences": file_refs,
            "Groups": groups,
            "HitAreas": hit_areas,
            "Layout": layout,
            "Parameters": param_list,
            "Deformers": deformers,
            "PreviewLayers": preview_layers,
        }

        model3_path = out / f"{character_name}.model3.json"
        model3_path.write_text(
            json.dumps(model3, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 9. Mesh data export (for Cubism Editor import)
        mesh_data_path = self._export_mesh_data(meshes, out, character_name)

        # 10. Cubism import guide
        guide_path = self.export_cubism_guide(str(out), character_name)

        log.success(f"Model3 exported: {model3_path}")

        return {
            "output_dir": str(out),
            "model3_json": str(model3_path),
            "moc3_ref": moc_filename,
            "textures": [str(out / f) for f in texture_files],
            "texture_files": texture_files,
            "physics": str(physics_path),
            "expressions": expr_manifest,
            "mesh_data": str(mesh_data_path),
            "guide": str(guide_path),
        }

    # ------------------------------------------------------------------
    # Component builders
    # ------------------------------------------------------------------

    def _build_file_references(
        self,
        moc: str,
        textures: List[str],
        physics: str,
        expressions: List[Dict[str, str]],
        pose: Optional[str] = None,
        motions: Optional[Dict[str, List[Dict]]] = None,
    ) -> Dict[str, Any]:
        """Assemble the FileReferences section."""
        refs: Dict[str, Any] = {
            "Moc": moc,
            "Textures": textures,
            "Physics": physics,
            "Expressions": expressions,
        }
        if pose:
            refs["Pose"] = pose
        if motions:
            refs["Motions"] = motions
        else:
            refs["Motions"] = {}
        return refs

    def _build_groups(self, builder_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build Groups section (EyeBlink, LipSync target parameter lists)."""
        groups: List[Dict[str, Any]] = [
            {
                "Target": "Parameter",
                "Name": "EyeBlink",
                "Ids": ["ParamEyeLOpen", "ParamEyeROpen"],
            },
            {
                "Target": "Parameter",
                "Name": "LipSync",
                "Ids": ["ParamMouthOpenY"],
            },
        ]

        # Add eye-ball tracking group if gaze params exist
        groups.append({
            "Target": "Parameter",
            "Name": "EyeBallMove",
            "Ids": ["ParamEyeBallX", "ParamEyeBallY"],
        })

        return groups

    @staticmethod
    def _classify_preview_group(name: str) -> str:
        """Map a rigging layer name to a web-preview part group."""
        n = name.lower()
        if any(k in n for k in ("hair", "bangs", "ahoge")):
            return "hair"
        if any(k in n for k in ("eyeball", "eye", "iris", "pupil", "lash")):
            return "eyes"
        if any(k in n for k in ("mouth", "lip", "teeth")):
            return "mouth"
        if any(k in n for k in ("face", "cheek", "nose", "skin", "blush")):
            return "face"
        if any(k in n for k in ("body", "chest", "clothes", "torso", "skirt", "arm", "leg")):
            return "body"
        return "other"

    def _export_preview_layers(
        self,
        layers: Dict[str, Image.Image],
        out: Path,
        character_name: str,
    ) -> List[Dict[str, Any]]:
        """Save one full-canvas PNG per layer and return a preview manifest.

        Fail-open: any layer that cannot be saved is skipped so model3.json
        export never breaks.
        """
        preview_dir = out / "layers"
        manifest: List[Dict[str, Any]] = []
        if not layers:
            return manifest
        try:
            preview_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning(f"Could not create preview layer dir: {exc}")
            return manifest

        for z, (name, img) in enumerate(layers.items()):
            try:
                safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
                fname = f"{safe}.png"
                rgba = img if img.mode == "RGBA" else img.convert("RGBA")
                rgba.save(str(preview_dir / fname))
                manifest.append({
                    "Name": name,
                    "Texture": f"layers/{fname}",
                    "Group": self._classify_preview_group(name),
                    "Width": int(rgba.width),
                    "Height": int(rgba.height),
                    "Z": z,
                })
            except Exception as exc:
                log.debug(f"Skipping preview layer '{name}': {exc}")
        log.info(f"Exported {len(manifest)} preview layer(s)")
        return manifest

    @staticmethod
    def _build_deformers(builder_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Serialise warp/rotation deformers into model3.json.

        Rotation deformers expose their pivot (the measured eyeball
        centroid) so downstream tools / the web preview can inspect where
        the gaze rotation is anchored.
        """
        # builder_result uses "deformer_tree"; the public build() return
        # value exposes the same data as "deformers". Accept both.
        tree = builder_result.get("deformer_tree") or builder_result.get("deformers") or {}
        defs = tree.get("deformers") if isinstance(tree, dict) else None
        out: List[Dict[str, Any]] = []
        if not defs:
            return out
        for d in defs:
            if not isinstance(d, dict):
                continue
            entry: Dict[str, Any] = {
                "Name": d.get("name"),
                "Type": "RotationDeformer" if d.get("type") == "rotation" else "WarpDeformer",
                "Parent": d.get("parent"),
                "Targets": d.get("targets", []),
            }
            if d.get("type") == "rotation":
                entry["Pivot"] = {"X": d["pivot"][0], "Y": d["pivot"][1]}
                entry["Angle"] = d.get("angle", 0.0)
            else:
                entry["Grid"] = {"Rows": d.get("grid_rows", 2), "Cols": d.get("grid_cols", 2)}
            out.append(entry)
        return out

    def _build_hit_areas(self, layers: Dict[str, Image.Image]) -> List[Dict[str, Any]]:
        """Build HitAreas section based on available layers.

        Head hit area covers face/hair layers; Body hit area covers body/clothes.
        Coordinates are normalised (0-1) and use Live2D's top-left origin.
        """
        areas: List[Dict[str, Any]] = []
        layer_names_lower = {k.lower(): k for k in layers.keys()}

        head_layers = [n for n in layers if any(
            kw in n.lower() for kw in ("face", "hair", "head", "eye", "brow", "nose", "mouth", "ear")
        )]
        body_layers = [n for n in layers if any(
            kw in n.lower() for kw in ("body", "chest", "torso", "clothes", "neck", "arm", "leg", "skirt")
        )]

        if head_layers:
            areas.append({
                "Id": "HitAreaHead",
                "Name": "Head",
                "Bounds": self._compute_bounds(layers, head_layers),
            })
        if body_layers:
            areas.append({
                "Id": "HitAreaBody",
                "Name": "Body",
                "Bounds": self._compute_bounds(layers, body_layers),
            })

        return areas

    @staticmethod
    def _compute_bounds(
        layers: Dict[str, Image.Image],
        names: List[str],
    ) -> Dict[str, float]:
        """Compute normalised bounding box across given layers (assumes 2048 canvas)."""
        import numpy as np
        min_x, min_y = float("inf"), float("inf")
        max_x, max_y = float("-inf"), float("-inf")
        found = False
        for name in names:
            img = layers.get(name)
            if img is None:
                continue
            arr = np.array(img)
            if arr.shape[2] < 4:
                continue
            alpha = arr[:, :, 3]
            ys, xs = np.where(alpha > 128)
            if len(xs) == 0:
                continue
            found = True
            min_x = min(min_x, float(xs.min()))
            min_y = min(min_y, float(ys.min()))
            max_x = max(max_x, float(xs.max()))
            max_y = max(max_y, float(ys.max()))
        if not found:
            return {"X": 0.3, "Y": 0.1, "Width": 0.4, "Height": 0.4}
        # Normalise assuming 2048 canvas (will be scaled by Cubism)
        canvas = 2048.0
        return {
            "X": round(min_x / canvas, 4),
            "Y": round(min_y / canvas, 4),
            "Width": round((max_x - min_x) / canvas, 4),
            "Height": round((max_y - min_y) / canvas, 4),
        }

    @staticmethod
    def _build_layout() -> Dict[str, Any]:
        """Return default model layout configuration."""
        return {
            "Width": 2048,
            "Height": 2048,
            "X": 0,
            "Y": 0,
            "CenterX": 0.0,
            "CenterY": 0.0,
            "PixelsPerUnit": 1.0,
        }

    # ------------------------------------------------------------------
    # Texture export
    # ------------------------------------------------------------------

    def _export_textures(
        self,
        layers: Dict[str, Image.Image],
        out_dir: Path,
        character_name: str,
        compress_webp: bool = False,
        webp_quality: int = 85,
    ) -> List[str]:
        """Pack and save texture atlases; return relative filenames.

        Args:
            compress_webp: 启用 WebP 压缩（体积缩小 30%~50%）。
            webp_quality:  WebP 质量 0~100。
        """
        ext = ".webp" if compress_webp else ".png"

        if not layers:
            log.warning("No layers provided; creating empty texture")
            empty = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
            fname = f"{character_name}.texture_00{ext}"
            if compress_webp:
                empty.save(str(out_dir / fname), "WEBP", quality=webp_quality)
            else:
                empty.save(str(out_dir / fname))
            return [fname]

        atlas_result = self._atlas.pack(layers)
        texture_files: List[str] = []
        for idx, atlas_img in enumerate(atlas_result["atlases"]):
            fname = f"{character_name}.texture_{idx:02d}{ext}"
            if compress_webp:
                atlas_img.save(str(out_dir / fname), "WEBP", quality=webp_quality)
            else:
                atlas_img.save(str(out_dir / fname))
            texture_files.append(fname)

        if compress_webp:
            # 输出压缩统计
            total_png_size = sum(
                len(layers.get(name, Image.new("RGBA", (0, 0))).tobytes())
                for name in layers
            )
            total_webp_size = sum(
                (out_dir / f).stat().st_size for f in texture_files
            )
            log.info(
                f"WebP 压缩完成: {len(texture_files)} 纹理 → "
                f"~{total_webp_size / 1024 / 1024:.1f} MB"
            )

        return texture_files

    # ------------------------------------------------------------------
    # Mesh data export (JSON for Cubism import)
    # ------------------------------------------------------------------

    def _export_mesh_data(
        self,
        meshes: Dict[str, Dict],
        out_dir: Path,
        character_name: str,
    ) -> Path:
        """Export mesh data as JSON (vertices, UVs, indices per layer)."""
        import numpy as np
        mesh_export: Dict[str, Any] = {}
        for name, mesh in meshes.items():
            verts = mesh.get("vertices")
            if verts is None or len(verts) == 0:
                continue
            indices = mesh.get("indices", [])
            norm = mesh.get("vertices_norm")
            mesh_export[name] = {
                "vertex_count": int(len(verts)),
                "triangle_count": int(len(indices)),
                "width": int(mesh.get("width", 0)),
                "height": int(mesh.get("height", 0)),
                "vertices": verts.tolist() if hasattr(verts, "tolist") else list(verts),
                "vertices_normalized": (
                    norm.tolist() if hasattr(norm, "tolist") else list(norm or [])
                ),
                "indices": [list(t) for t in indices],
            }
            # Per-vertex skinning weights (present when the rigging pipeline
            # computed bone bindings). Omitted for meshes without weights so
            # the output stays backward compatible.
            weights = mesh.get("weights")
            if weights and isinstance(weights, dict) and weights.get("weights"):
                mesh_export[name]["bone_names"] = list(weights.get("bone_names", []))
                mesh_export[name]["weights"] = [
                    list(w) for w in weights["weights"]
                ]
        path = out_dir / f"{character_name}.meshes.json"
        path.write_text(
            json.dumps(mesh_export, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Physics helpers
    # ------------------------------------------------------------------

    def _build_default_physics(self, layers: Dict[str, Image.Image]) -> Dict[str, Any]:
        """Build a default physics configuration based on available layers."""
        self._physics.reset()
        hair = [n for n in layers if "hair" in n.lower()]
        skirt = [n for n in layers if "skirt" in n.lower()]
        has_ears = any("ear" in n.lower() and "animal" in n.lower() for n in layers)
        has_tail = any("tail" in n.lower() for n in layers)

        if hair:
            self._physics.build_hair_physics(hair)
        self._physics.build_body_physics()
        self._physics.build_breathing_physics()
        if skirt:
            self._physics.build_skirt_physics(skirt)
        self._physics.build_ear_tail_physics(has_ears, has_tail)

        return self._physics.to_physics3_json()

    # ------------------------------------------------------------------
    # Cubism Editor import guide
    # ------------------------------------------------------------------

    def export_cubism_guide(self, output_dir: str, character_name: str = "character") -> str:
        """Write a detailed markdown guide for importing into Cubism Editor.

        Returns:
            Absolute path to the written guide.
        """
        out = Path(output_dir)
        guide_path = out / f"{character_name}_CUBISM_IMPORT_GUIDE.md"
        guide_path.write_text(self._build_guide_text(character_name), encoding="utf-8")
        log.info(f"Cubism import guide written to {guide_path}")
        return str(guide_path)

    @staticmethod
    def _build_guide_text(character_name: str) -> str:
        """Build the markdown guide text."""
        return f"""# Cubism Editor Import Guide — {character_name}

Generated: {time.strftime("%Y-%m-%d %H:%M:%S")}

## What This Package Contains

| File | Purpose |
|------|---------|
| `{character_name}.model3.json` | Cubism 4 model definition |
| `{character_name}.moc3` | **NOT included** — generated by Cubism Editor |
| `{character_name}.texture_00.png` | Texture atlas(es) |
| `{character_name}.physics3.json` | Physics settings (hair, body, breath, skirt) |
| `{character_name}.meshes.json` | Per-layer mesh data (vertices, UVs, triangles) |
| `expressions/*.exp3.json` | 28 facial expressions |
| `{character_name}.psd` | Original layered PSD (if provided) |

## Step-by-Step Import

### 1. Prepare the PSD
1. Open Adobe Photoshop or a PSD-capable editor.
2. Ensure each body part is on a named layer.
3. Recommended layer names follow the 52-layer Live2D standard:
   - `Hair_Back`, `Hair_Front`, `Hair_Side_L/R`, `Hair_Top`
   - `Face_Base`, `Face_Blush`, `Ear_L/R`
   - `Eye_L/R`, `Eyeball_L/R`, `Eyelash_L/R`, `Brow_L/R`
   - `Nose`, `Mouth_UpperLip`, `Mouth_LowerLip`, `Mouth_Cavity`
   - `Neck`, `Chest`, `Waist_Hips`, `Clothes_Inner/Outer`
   - `UpperArm_Back_L/R`, `Forearm_Back_L/R`
   - `Thigh_L/R`, `Calf_L/R`, `Foot_L/R`

### 2. Import into Cubism Editor
1. Launch Live2D Cubism Editor 4.2+.
2. **File > Open Model** and select `{character_name}.model3.json`.
3. When prompted, point to the PSD file.
4. Cubism Editor will auto-detect layers matching the model definitions.

### 3. Generate the Moc3 File
The `.moc3` binary is proprietary and cannot be generated outside Cubism Editor.
1. After importing and verifying the model, **File > Export > Export as .moc3 file**.
2. Save as `{character_name}.moc3` in the same directory as `model3.json`.
3. The model3.json already references this filename.

### 4. Apply Mesh Data
The `{character_name}.meshes.json` file contains Delaunay-triangulated meshes
for each layer. In Cubism Editor:
1. Select an ArtMesh.
2. In the **Mesh** palette, choose **Edit Mesh**.
3. Use the vertex counts from `meshes.json` as a guide for mesh density.
4. Apply UV coordinates from the JSON (u0, v0, u1, v1 per layer).

### 5. Set Up Parameters
All standard Cubism 4 parameters are already defined in model3.json:
- `ParamAngleX/Y/Z` — Head rotation
- `ParamBodyAngleX/Y/Z` — Body rotation
- `ParamEyeLOpen/ParamEyeROpen` — Eye blink
- `ParamEyeBallX/Y` — Gaze direction
- `ParamMouthForm/ParamMouthOpenY` — Mouth
- `ParamBrowL/R (Y, Angle, Form)` — Eyebrows
- `ParamBreath` — Breathing
- `ParamCheek`, `ParamTears` — Special effects
- `ParamHairSwing`, `ParamBodySway` — Custom

### 6. Apply Physics
1. In Cubism Editor, open **Physics > Load Physics Settings**.
2. Select `{character_name}.physics3.json`.
3. Verify pendulum settings for:
   - HairFront / HairBack (pendulum swing)
   - BodyBounce (body bounce on movement)
   - Breathing (slow cyclic breathing)
   - Skirt (if present, cloth sway)

### 7. Load Expressions
The `expressions/` folder contains 28 pre-built `.exp3.json` files.
In Cubism Editor:
1. Open the **Expressions** palette.
2. The expressions are automatically referenced by model3.json.
3. Preview each expression by clicking its name.

### 8. VTube Studio / VSeeFace Compatibility
After generating moc3:
1. Copy the entire model folder to VTube Studio's `Live2DModels/` directory.
2. In VTube Studio, select the model from the model list.
3. VTube Studio reads model3.json directly — no conversion needed.

For VSeeFace, place the folder in `VSeeFace/Models/VSeeFace_Models/`.

## Bone Hierarchy (32 bones)

The bone tree follows the standard Live2D layout:
```
Root
 +-- Body
 |    +-- Torso (Chest + Waist)
 |    +-- Neck -> Head
 |    |    +-- Face, Hair_Back/Front/Side/Top, Ears
 |    |    +-- Eye_L -> Eyeball_L, Eyelash_L, Brow_L
 |    |    +-- Eye_R -> Eyeball_R, Eyelash_R, Brow_R
 |    |    +-- Nose, Mouth
 |    +-- ArmBack_L/R, Skirt, Leg_L/R
```

## Deformers

Warp deformers are provided for:
- HairFrontSwing (3x3 grid) — front hair swing
- HairBackSwing (4x2 grid) — back hair swing
- BodySway (2x2 grid) — torso sway
- SkirtSway (4x3 grid) — skirt cloth
- BreathChest (2x2 grid) — breathing chest expansion

Rotation deformers:
- EyeTrack_L / EyeTrack_R — eye gaze pivot

## Notes

- Textures are packed at 2048x2048 with 2px padding to prevent bleeding.
- Physics uses pendulum model with gravity and damping parameters.
- The eye blink group automatically blinks both eyes on `EyeBlink`.
- Lip sync uses `ParamMouthOpenY` driven by audio volume.
- All parameter ranges follow the Cubism 4 SDK specification.
"""

    # ------------------------------------------------------------------
    # Packaging
    # ------------------------------------------------------------------

    def package_model(self, output_dir: str, character_name: str = "character") -> str:
        """Create a zip archive containing all model files.

        Args:
            output_dir: Directory containing the exported model files.
            character_name: Model name (used for the archive name).

        Returns:
            Absolute path to the created zip file.
        """
        out = Path(output_dir)
        zip_path = out / f"{character_name}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in out.rglob("*"):
                if fpath.is_file() and fpath != zip_path:
                    arcname = fpath.relative_to(out)
                    zf.write(fpath, str(arcname))

        log.info(f"Model packaged: {zip_path}")
        return str(zip_path)

    # ------------------------------------------------------------------
    # Direct model3.json export (convenience)
    # ------------------------------------------------------------------

    def export_model3_json(self, model3: Dict[str, Any], output_path: str) -> str:
        """Write a pre-built model3 dict to disk.

        Args:
            model3: The complete model3.json dict.
            output_path: Target file path.

        Returns:
            Absolute path written.
        """
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(model3, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
