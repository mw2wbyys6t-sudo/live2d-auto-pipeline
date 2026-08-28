#!/usr/bin/env python3
"""End-to-end Live2D model building pipeline.

Orchestrates mesh generation, UV layout, bone hierarchy, deformers,
parameters, expressions, physics, texture atlas baking, model3.json
export, and validation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger
from live2d_builder.mesh.generator import MeshGenerator
from live2d_builder.mesh.uv_unwrapper import UVUnwrapper
from live2d_builder.bones.deformers import BoneHierarchy, DeformerHierarchy
from live2d_builder.blendshapes.parameters import ParameterSet, STANDARD_PARAMETERS
from live2d_builder.blendshapes.expressions import ExpressionBuilder
from live2d_builder.physics.config import PhysicsBuilder
from live2d_builder.exporter.model3_exporter import Model3Exporter
from live2d_builder.exporter.texture_atlas import TextureAtlas
from live2d_builder.validator.model_validator import ModelValidator

log = get_logger("rigging.pipeline")


class Live2DBuilder:
    """Full Live2D Cubism 4 model build pipeline.

    Usage::

        builder = Live2DBuilder(output_dir="./out", character_name="hero")
        result = builder.build(layers=my_layer_dict)
        # result["model3_json"] -> path to model3.json

    The pipeline performs these steps in order:
    1. Generate meshes per layer (Delaunay triangulation)
    2. Layout UVs (texture atlas packing)
    3. Build bone hierarchy (32-bone skeleton)
    4. Set up warp/rotation deformers
    5. Define parameter set (28 standard + custom parameters)
    6. Generate 28 expressions
    7. Build physics configuration
    8. Bake texture atlases
    9. Export model3.json + companion files
    10. Validate the output
    """

    TOTAL_STEPS = 10

    def __init__(
        self,
        output_dir: str,
        character_name: str = "character",
        atlas_size: int = 2048,
        mesh_spacing: int = 20,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.character_name = character_name
        self.atlas_size = atlas_size

        # Sub-components
        self.mesh_gen = MeshGenerator(internal_spacing=mesh_spacing, contour_spacing=12)
        self.uv_unwrapper = UVUnwrapper(atlas_size=atlas_size, padding=2, algorithm="skyline")
        self.bones = BoneHierarchy()
        self.deformers = DeformerHierarchy()
        self.params = ParameterSet()
        self.expressions = ExpressionBuilder()
        self.physics = PhysicsBuilder(fps=60)
        self.exporter = Model3Exporter(max_atlas_size=atlas_size)
        self.validator = ModelValidator()

        # Build state (populated during build())
        self._meshes: Dict[str, Dict] = {}
        self._uv_data: Dict[str, Dict] = {}
        self._bone_tree: Dict[str, Any] = {}
        self._deformer_tree: Dict[str, Any] = {}
        self._physics3: Dict[str, Any] = {}
        self._centroids: Dict[str, Tuple[float, float]] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def build(
        self,
        layers: Dict[str, Image.Image],
        part_masks: Optional[Dict[str, np.ndarray]] = None,
    ) -> Dict[str, Any]:
        """Run the full build pipeline.

        Args:
            layers:     Ordered dict of ``{layer_name: PIL.Image}`` in RGBA.
            part_masks: Optional dict of ``{layer_name: np.ndarray}`` binary
                        masks for parts (used to refine mesh boundaries).

        Returns:
            dict with paths to all generated files and intermediate data.
        """
        t0 = time.time()
        log.section(f"Building Live2D model: {self.character_name}")

        # Step 1: Meshes
        log.step(1, self.TOTAL_STEPS, "Generating meshes")
        self._meshes = self._generate_meshes(layers)

        # Step 2: UV layout
        log.step(2, self.TOTAL_STEPS, "Laying out UVs")
        self._uv_data = self._layout_uvs(self._meshes, {})

        # Step 3: Bones
        log.step(3, self.TOTAL_STEPS, "Building bone hierarchy")
        centroids = self._compute_centroids(layers)
        self._centroids = centroids
        self._bone_tree = self._build_bones(layers, centroids)
        # Step 3b: per-vertex skinning weights (fail-open)
        self._assign_bone_weights()

        # Step 4: Deformers
        log.step(4, self.TOTAL_STEPS, "Setting up deformers")
        self._deformer_tree = self._setup_deformers(
            self._bone_tree, self._meshes, list(layers.keys()), centroids
        )

        # Step 5: Parameters
        log.step(5, self.TOTAL_STEPS, "Configuring parameters")
        param_defs = self._setup_parameters()
        log.info(f"Parameters: {param_defs['count']} defined")

        # Step 6: Expressions
        log.step(6, self.TOTAL_STEPS, "Generating expressions")
        expr_list = self._generate_expressions()
        log.info(f"Expressions: {len(expr_list)} generated")

        # Step 7: Physics
        log.step(7, self.TOTAL_STEPS, "Building physics")
        self._physics3 = self._generate_physics(layers)

        # Step 8: Bake textures
        log.step(8, self.TOTAL_STEPS, "Baking texture atlases")
        self._bake_textures(layers)

        # Step 9: Export
        log.step(9, self.TOTAL_STEPS, "Exporting model3.json")
        builder_result = {
            "layers": layers,
            "meshes": self._meshes,
            "uv_data": self._uv_data,
            "bone_tree": self._bone_tree,
            "deformer_tree": self._deformer_tree,
            "parameters": param_defs,
            "expressions": expr_list,
            "physics3": self._physics3,
        }
        export_result = self.exporter.export(
            builder_result=builder_result,
            output_dir=str(self.output_dir),
            character_name=self.character_name,
        )

        # Step 10: Validate
        log.step(10, self.TOTAL_STEPS, "Validating model")
        validation = self.validator.validate_all(str(self.output_dir))
        compatibility = self.validator.check_cubism_compatibility(str(self.output_dir))

        elapsed = time.time() - t0
        log.section("Build complete")
        cn = self.character_name
        nm = len(self._meshes)
        ne = len(expr_list)
        vv = validation["valid"]
        log.success(f"Built '{cn}' in {elapsed:.1f}s — meshes: {nm}, expressions: {ne}, valid: {vv}")

        # Save a build metadata file
        meta = {
            "character_name": self.character_name,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(elapsed, 2),
            "layer_count": len(layers),
            "mesh_count": len(self._meshes),
            "expression_count": len(expr_list),
            "parameter_count": len(param_defs),
            "validation_valid": validation["valid"],
            "validation_errors": validation["errors"],
            "compatibility": {
                k: v["compatible"] for k, v in compatibility.items()
            },
            "output_dir": str(self.output_dir),
            "model3_json": export_result["model3_json"],
        }
        meta_path = self.output_dir / "build_meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Save mesh guide (backward compatible with old pipeline)
        mesh_guide = self._write_mesh_guide()

        return {
            **export_result,
            "meshes": self._meshes,
            "uv_data": self._uv_data,
            "bone_tree": self._bone_tree,
            "deformers": self._deformer_tree,
            "parameters": param_defs,
            "expressions": expr_list,
            "physics3": self._physics3,
            "validation": validation,
            "compatibility": compatibility,
            "mesh_guide": str(mesh_guide),
            "build_meta": str(meta_path),
            "elapsed_seconds": round(elapsed, 2),
        }

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _generate_meshes(self, layers: Dict[str, Image.Image]) -> Dict[str, Dict]:
        """Generate a Delaunay mesh per non-empty layer.

        Uses the combined contour+grid mesh for best quality on organic
        character shapes.
        """
        meshes: Dict[str, Dict] = {}
        for name, layer in layers.items():
            if not isinstance(layer, Image.Image):
                log.warning(f"Skipping non-image layer: {name}")
                continue
            try:
                mesh = self.mesh_gen.generate_combined_mesh(
                    layer,
                    grid_spacing=self.mesh_gen.internal_spacing,
                    contour_spacing=self.mesh_gen.contour_spacing,
                )
                if len(mesh["vertices"]) >= 3 and len(mesh["indices"]) > 0:
                    meshes[name] = mesh
                    nv = len(mesh["vertices"])
                    nt = len(mesh["indices"])
                    log.debug(f"Mesh '{name}': {nv} verts, {nt} tris")
                else:
                    log.debug(f"Skipping empty/too-small layer: {name}")
            except Exception as exc:
                log.error(f"Failed to generate mesh for '{name}': {exc}")

        log.info(f"Generated meshes for {len(meshes)}/{len(layers)} layers")
        return meshes

    def _layout_uvs(
        self,
        meshes: Dict[str, Dict],
        atlas: Dict[str, Any],
    ) -> Dict[str, Dict]:
        """Pack meshes into the texture atlas and compute UV coordinates."""
        if not meshes:
            return {}
        result = self.uv_unwrapper.pack_meshes(meshes)
        layout = self.uv_unwrapper.get_atlas_layout()
        npages = layout["counts"]["pages"]
        util = layout["counts"]["utilisation"] * 100
        log.info(f"UV layout: {npages} page(s), utilisation {util:.1f}%")
        return result["uvs"]

    def _build_bones(
        self,
        layers: Dict[str, Image.Image],
        centroids: Dict[str, Tuple[float, float]],
    ) -> Dict[str, Any]:
        """Build the 32-bone hierarchy adapting to available layer groups."""
        return self.bones.build(list(layers.keys()), centroids=centroids)

    def _assign_bone_weights(self) -> None:
        """Attach per-vertex skinning weights to every generated mesh.

        Fail-open: any problem (missing bone tree, degenerate mesh) leaves
        the mesh without weights rather than aborting the build, so the
        exported model3.json remains loadable.
        """
        try:
            if not self._meshes or not self._bone_tree:
                return
            bone_positions = self.bones.get_bone_positions(self._centroids or {})
            assignments = self._bone_tree.get("layer_assignments", {})
            for name, mesh in self._meshes.items():
                try:
                    assigned = assignments.get(name, "Head")
                    parent = BoneHierarchy.STANDARD_BONES.get(assigned, {}).get("parent")
                    mesh["weights"] = self.mesh_gen.compute_bone_weights(
                        mesh, bone_positions, assigned, parent
                    )
                except Exception as exc:
                    log.debug(f"Skinning weights skipped for '{name}': {exc}")
        except Exception as exc:
            log.warning(f"Bone weight assignment failed (non-fatal): {exc}")

    def _setup_deformers(
        self,
        bone_tree: Dict[str, Any],
        meshes: Dict[str, Dict],
        layer_names: List[str],
        centroids: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Any]:
        """Create warp/rotation deformers for hair, body, and eyes."""
        return self.deformers.build(
            bone_tree, meshes, layer_names=layer_names, centroids=centroids
        )

    def _setup_parameters(self) -> Dict[str, Any]:
        """Return the standard parameter set (28 Cubism4 params + 2 custom)."""
        defaults = self.params.get_default_values()
        cubism_params = self.params.export_cubism_params()
        blendshapes = self.params.get_blendshapes()
        return {
            "defaults": defaults,
            "cubism_params": cubism_params,
            "blendshapes": blendshapes,
            "count": len(self.params),
        }

    def _generate_expressions(self) -> List[Dict[str, Any]]:
        """Generate all 28 standard expressions."""
        return self.expressions.build_all()

    @staticmethod
    def _layer_alpha_height(img: Image.Image, default: int = 80) -> int:
        """Measure the vertical span (px) of the opaque region of a layer.

        Returns the difference between the bottom-most and top-most rows with
        alpha > 32; ``default`` when the layer is fully transparent.
        """
        try:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            arr = np.array(img)
            alpha = arr[:, :, 3]
            rows = np.where((alpha > 32).any(axis=1))[0]
            if len(rows) == 0:
                return default
            return int(rows.max() - rows.min())
        except Exception:
            return default

    def _generate_physics(self, layers: Dict[str, Image.Image]) -> Dict[str, Any]:
        """Build physics3.json configuration for hair, body, breath, etc.

        Hair/skirt spring stiffness and pendulum length are geometry-aware:
        the alpha span of each layer is measured so that a short bob swings
        stiffly while floor-length hair flows softly.
        """
        hair_layers = [n for n in layers if "hair" in n.lower()]
        skirt_layers = [n for n in layers if "skirt" in n.lower()]
        has_ears = any("animal_ear" in n.lower() or "kemomimi" in n.lower() for n in layers)
        has_tail = any("tail" in n.lower() for n in layers)

        hair_metrics = [
            (n, self._layer_alpha_height(layers[n])) for n in hair_layers
        ]
        skirt_metrics = [
            (n, self._layer_alpha_height(layers[n])) for n in skirt_layers
        ]

        self.physics.reset()
        if hair_layers:
            self.physics.build_hair_physics(hair_layers, metrics=hair_metrics)
        self.physics.build_body_physics()
        self.physics.build_breathing_physics()
        if skirt_layers:
            self.physics.build_skirt_physics(skirt_layers, metrics=skirt_metrics)
        self.physics.build_ear_tail_physics(has_ears, has_tail)

        return self.physics.to_physics3_json()

    def _bake_textures(self, layers: Dict[str, Image.Image]) -> List[str]:
        """Bake layers into texture atlas images based on UV layout."""
        if not self._uv_data:
            log.warning("No UV data; texture baking skipped")
            return []
        try:
            atlas_path = self.output_dir / f"{self.character_name}_baked.png"
            written = TextureAtlas(max_size=self.atlas_size).bake_atlas(
                layers=layers,
                meshes=self._meshes,
                uv_data=self._uv_data,
                output_path=str(atlas_path),
            )
            log.info(f"Baked {len(written)} texture page(s)")
            return written
        except Exception as exc:
            log.error(f"Texture baking failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Packaging
    # ------------------------------------------------------------------

    def export_cubism_package(self) -> str:
        """Create a zip package with all model files and the import guide.

        Returns:
            Absolute path to the created .zip file.
        """
        return self.exporter.package_model(
            output_dir=str(self.output_dir),
            character_name=self.character_name,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_centroids(
        layers: Dict[str, Image.Image],
    ) -> Dict[str, Tuple[float, float]]:
        """Compute alpha-weighted centroids for each layer."""
        centroids: Dict[str, Tuple[float, float]] = {}
        for name, img in layers.items():
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            arr = np.array(img)
            alpha = arr[:, :, 3]
            mask = alpha > 128
            if mask.sum() == 0:
                continue
            ys, xs = np.where(mask)
            centroids[name] = (float(xs.mean()), float(ys.mean()))
        return centroids

    def _write_mesh_guide(self) -> Path:
        """Write a per-layer mesh metadata JSON guide."""
        guide_path = self.output_dir / "mesh_guide.json"
        meta = {
            name: {
                "vertex_count": int(len(mesh["vertices"])),
                "triangle_count": int(len(mesh["indices"])),
                "width": int(mesh.get("width", 0)),
                "height": int(mesh.get("height", 0)),
            }
            for name, mesh in self._meshes.items()
        }
        guide_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return guide_path


# ======================================================================
# Backward-compatible alias (matches the original API)
# ======================================================================

class RiggingPipeline:
    """Compatibility wrapper around :class:`Live2DBuilder`.

    Keeps the old ``run(layers, output_dir, character_name)`` interface
    so existing callers don't break.
    """

    def __init__(self) -> None:
        self.mesh_generator = MeshGenerator()
        self.deformer_hierarchy = DeformerHierarchy()
        self.parameters = ParameterSet()

    def run(
        self,
        layers: Dict[str, Image.Image],
        output_dir: str,
        character_name: str = "character",
    ) -> Dict[str, Any]:
        """Run the rigging pipeline (backward-compatible entry point)."""
        builder = Live2DBuilder(
            output_dir=output_dir,
            character_name=character_name,
        )
        result = builder.build(layers)
        return self._align_model3_key(result, output_dir)

    @staticmethod
    def _align_model3_key(result: Any, output_dir: str) -> Dict[str, Any]:
        """Guarantee ``result['model3_json']`` points at a ``.model3.json`` file.

        The exporter historically used a few different key names
        (``model3_json`` / ``model_json`` / ``model3_path``) and callers such
        as ``core.workflow`` sometimes received a ``build_meta`` path instead
        of the real model3 path. This normalises the result so downstream
        export/deploy steps always find a usable path (fail-open: if no key
        matches, we scan the output directory for a ``.model3.json`` file).
        """
        if not isinstance(result, dict):
            return result

        def _is_model3(value: Any) -> bool:
            return isinstance(value, str) and value.endswith(".model3.json")

        existing = result.get("model3_json")
        if not _is_model3(existing):
            for key in ("model3_json", "model_json", "model3_path"):
                candidate = result.get(key)
                if _is_model3(candidate):
                    result["model3_json"] = candidate
                    break

        if not _is_model3(result.get("model3_json")):
            # Last-resort: scan the output / build_meta directory.
            search_dirs = [output_dir]
            build_meta = result.get("build_meta")
            if isinstance(build_meta, str):
                search_dirs.insert(0, str(Path(build_meta).parent))
            for base in search_dirs:
                try:
                    matches = sorted(Path(base).glob("*.model3.json"))
                except OSError:
                    matches = []
                if matches:
                    result["model3_json"] = str(matches[0])
                    break

        return result
