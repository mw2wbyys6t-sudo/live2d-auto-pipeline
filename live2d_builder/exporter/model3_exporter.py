#!/usr/bin/env python3
"""Export complete Live2D Cubism 4 model3.json and companion files.

Generates:
- <name>.model3.json  (Cubism 4 model definition)
- <name>.texture_NN.png (texture atlases)
- <name>.physics3.json
- expressions/*.exp3.json (28 expressions)
- motions/*.motion3.json (仅覆盖真有键形驱动的参数；无可动参数则不产出)
- Cubism Editor import guide (markdown)
- Zip package ready for distribution

Note: 本模块只写 model3.json 与配套文件。`.moc3` 由
`live2d_builder.exporter.moc3_pipeline.compile_export_moc3`（pipeline 第 9b 步）
编译并写出：通过官方 Cubism Core 一致性验收才落盘，`build_meta.json` 的
`moc3.runtime_ready` 如实反映状态。若编译被跳过或失败，model3.json 中的
Moc 引用仍指向占位路径，需按导入指南在 Cubism Editor 手工生成。
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
from live2d_builder.motion.motion3 import Motion3Builder
from live2d_builder.physics.config import PhysicsBuilder, prune_physics_to_parameters

log = get_logger("exporter.model3")


class Model3Exporter:
    """Export a complete Cubism 4 model package."""

    def __init__(self, max_atlas_size: int = 2048) -> None:
        self.max_atlas_size = max_atlas_size
        self._atlas = TextureAtlas(max_size=max_atlas_size)
        self.motion_files: List[str] = []
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
        drivable_parameters: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, str]:
        """Export all model files to ``output_dir``.

        Args:
            builder_result:  Output of :class:`Live2DBuilder.build`.
            output_dir:      Directory to write files into.
            character_name:  Base filename for the model.
            compress_webp:   导出纹理时转换为 WebP（体积缩小 30%~50%）。
                             Cubism 4/5 SDK 均支持 WebP 纹理加载。
            webp_quality:    WebP 质量，0~100，默认 85。
            drivable_parameters: 参数 id -> 已编译键形值序列。只有落在这个表里
                             的参数才会被排进 motion；不给就不产出动画文件。

        Returns:
            dict with paths to all generated files.
        """
        if not character_name or character_name in (".", "..") or any(c in character_name for c in "/\\:\x00"):
            raise ValueError("character_name must be a safe filename")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        layers: Dict[str, Image.Image] = builder_result.get("layers", {})
        meshes: Dict[str, Dict] = builder_result.get("meshes", {})
        physics_data: Dict = builder_result.get("physics3")
        if physics_data is None:
            physics_data = self._build_default_physics(layers)
        # 只保留两端都已声明的物理链：引用缺失参数的链会被内核静默丢弃，
        # 留下文件只会造出「有物理、却一动不动」的假产物。
        physics_data = prune_physics_to_parameters(
            physics_data,
            (builder_result.get("parameters") or {}).get("cubism_params"))

        # 1. Pack textures
        texture_files = self._export_textures(layers, out, character_name)

        # 2. Build file references
        moc_filename = f"{character_name}.moc3"

        # 3. Expressions
        expr_manifest = self._expressions.export_to_directory(str(out))

        # 4. Physics（一条链都不剩时不写文件，也不在清单里留引用）
        physics_filename = ""
        kept_settings = (physics_data or {}).get("PhysicsSettings") or []
        if kept_settings:
            physics_path = out / f"{character_name}.physics3.json"
            physics_path.write_text(
                json.dumps(physics_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            physics_filename = f"{character_name}.physics3.json"
        else:
            log.warning("没有可用的物理链（引用了模型里未声明的参数），"
                        "physics3.json 不写出")

        # 5. Groups (EyeBlink, LipSync)
        groups = self._build_groups(builder_result)

        # 6. Hit areas
        hit_areas = self._build_hit_areas(layers)

        # 7. Deformers (warp grids + eye rotation pivots). The eye rotation
        # deformers carry the *measured* eyeball centroid as their pivot so
        # the gaze rotation centre is inspectable from model3.json.
        deformers = self._build_deformers(builder_result)

        # 7c. Per-layer preview textures for the browser player. The packed
        # atlases above are bin-packed crops (correct for Cubism, wrong for a
        # single-quad preview), so we also export one full-canvas PNG per
        # layer plus a manifest. The web player stacks these into a coherent
        # character and can deform each part independently.
        preview_layers = self._export_preview_layers(layers, out, character_name)

        # 8. Motions. 曲线只覆盖真的有键形驱动的参数：给一个动不了的参数排
        #    曲线会得到「文件齐全但画面一动不动」的假产物。
        motions_manifest = self._export_motions(
            out, character_name, drivable_parameters)

        # 8b. Assemble model3.json
        file_refs = self._build_file_references(
            moc=moc_filename,
            textures=texture_files,
            physics=physics_filename,
            expressions=expr_manifest,
            motions=motions_manifest,
        )
        param_list = self._params.export_cubism_params()

        model3: Dict[str, Any] = {
            "Version": 3,
            "FileReferences": file_refs,
            "Groups": groups,
            "HitAreas": hit_areas,
            "Parameters": param_list,
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
            "atlas_uvs": getattr(self, "_last_atlas_uvs", {}),
            "textures": [str(out / f) for f in texture_files],
            "texture_files": texture_files,
            "physics": str(out / physics_filename) if physics_filename else "",
            "expressions": expr_manifest,
            "motions": motions_manifest,
            "motion_files": self.motion_files,
            "mesh_data": str(mesh_data_path),
            "guide": str(guide_path),
        }

    # ------------------------------------------------------------------
    # Component builders
    # ------------------------------------------------------------------

    def _export_motions(self, out: Path, character_name: str,
                        drivable_parameters: Optional[Dict[str, List[float]]]
                        ) -> Dict[str, List[dict]]:
        """生成并写出 idle 动作。

        没有可动参数就返回空清单（``Motions`` 保持 ``{}``）—— 给动不了的参数
        排曲线会得到「文件齐全但画面一动不动」的假产物。
        """
        self.motion_files = []
        if not drivable_parameters:
            return {}
        builder = Motion3Builder()
        document = builder.build_idle(drivable_parameters)
        if document is None:
            return {}
        manifest = builder.export_to_directory(
            str(out), character_name, {"idle": document})
        self.motion_files = [str(out / entry["File"])
                             for entries in manifest.values() for entry in entries]
        return manifest

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
                fname = f"{z:03d}_{safe}.png"
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

    # ------------------------------------------------------------------
    # Texture export
    # ------------------------------------------------------------------

    def _export_textures(
        self,
        layers: Dict[str, Image.Image],
        out_dir: Path,
        character_name: str,
    ) -> List[str]:
        """Pack and save texture atlases; return relative filenames."""
        if not layers:
            log.warning("No layers provided; creating empty texture")
            empty = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
            fname = f"{character_name}.texture_00.png"
            empty.save(str(out_dir / fname))
            return [fname]

        atlas_result = self._atlas.pack(layers)
        # 供 moc3 编译等下游使用：每层在图集中的归一化 UV 摆放
        self._last_atlas_uvs = dict(atlas_result.get("uvs", {}))
        texture_files: List[str] = []
        for idx, atlas_img in enumerate(atlas_result["atlases"]):
            fname = f"{character_name}.texture_{idx:02d}.png"
            atlas_img.save(str(out_dir / fname))
            texture_files.append(fname)
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
        return f"""# Cubism 制作素材说明 — {character_name}

本文件包是制作准备素材，不是可直接部署的 Live2D 模型。

- PNG 图层及 PSD（如果提供）用于导入 Cubism Editor。
- model3.json 是运行时描述，不是可编辑的 cmo3 工程；不能打开它自动恢复绑定。
- meshes.json、Parameters 和 Deformers 是本项目的辅助数据，不会自动成为 Cubism 绑定。
- 当前导出器没有实现 moc3 编译，也未完成 Cubism Editor 自动绑定集成。

## 正确流程
1. 将 PSD 导入 Cubism Editor，检查图层及叠放顺序。
2. 创建或检查 ArtMesh、变形器、参数关键形、遮挡和物理效果。
3. 保存 cmo3 工程，再用编辑器导出 moc3 和配套运行时文件。
4. 使用编辑器生成的纹理和描述文件整包替换，不要混用本项目重排的纹理。
5. 在 Cubism SDK 或 VTube Studio 中实际加载，验证眨眼、嘴型、转头和物理。

VSeeFace 使用 VRM 三维角色，不支持将此 Cubism 素材包直接部署为角色。
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
