#!/usr/bin/env python3
"""
Live2D Master Agent - Unified Workflow Engine (v10.0)

Full pipeline: Text-to-Image -> QA -> Optimize -> Layer -> PSD -> 52-layer mapping -> Pet

P0-3 FIX: Uses KMeansLayerer (v6) as default layerer.
P1-2 FIX: Cleans up temporary files on failure.
P1-4 FIX: Uses configurable timeout from config.
DEF-003: Seedream/ARK provider integrated.
DEF-004: 52-layer standard mapping with parameter/physics config.
DEF-007: Unified logging throughout.
v10.0:  Character consistency system, semantic segmentation, Live2D export.
"""

import os
import sys

# When invoked with --json we want stdout reserved for the JSON payload. This
# env var is read by core.logger at import time, so set it as early as
# possible (the CLI flips it on after parsing args too).
if "--json" in sys.argv:
    os.environ["LIVE2D_JSON_MODE"] = "1"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import time
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Union

from PIL import Image, ImageEnhance, ImageFilter
import numpy as np

from core.version import __version__, FULL_VERSION_STRING
from core.config import config
from core.logger import get_logger
from core.image_gen.router import ProviderRouter, get_router
from core.segment_engine.kmeans import KMeansLayerer
from core.segment_engine.semantic import SemanticLayerer
from core.segment_engine.composer import LayerComposer
from core.segment_engine.layers52 import Layer52Generator
from core.segment_engine.part_identifier import PartIdentifier
from core.psd.creator import PSDCreator
from core.psd.validator import PSDValidator
from drivers.desktop_pet.animator import DesktopPetAnimator
from core.qa.engine import QAEngine
from core.security import sanitize_prompt, validate_image_path

log = get_logger("workflow")


class WorkflowEngine:
    """JSON-state-machine-based workflow engine.

    Pipeline states:
    1. idle -> generating -> qa_check -> optimizing -> layering -> psd_export -> mapping -> done
    Each state transition is logged and state is serializable.

    v10.0 additions:
    - character_consistency: lock generation to a CharacterCard
    - use_semantic_segmentation: use SemanticLayerer with KMeans fallback
    - export_live2d: invoke Live2DBuilder for model3 export
    """

    STATES = ["idle", "generating", "qa_check", "optimizing", "layering",
              "rigging", "psd_export", "mapping", "live2d_export", "pet_deploy", "done", "error"]

    def __init__(
        self,
        output_dir: Optional[str] = None,
        k_clusters: int = 12,
        provider: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        character_consistency: Any = None,
        use_semantic_segmentation: bool = True,
        export_live2d: bool = False,
    ):
        """Initialize the workflow engine.

        Args:
            output_dir: Directory for output artifacts.
            k_clusters: Number of K-means clusters for fallback layering.
            provider: Image generation provider name.
            width: Output image width.
            height: Output image height.
            character_consistency: A CharacterCard instance or character_id
                string. If provided, the character's style prompt and
                negative prompt are merged into generation and the result
                is optionally saved back to the character.
            use_semantic_segmentation: If True, use SemanticLayerer
                (falls back to KMeans if model unavailable).
            export_live2d: If True, run Live2DBuilder to produce a
                model3.json scaffold.
        """
        self.output_dir = Path(output_dir or config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.k_clusters = k_clusters
        self.provider_name = provider
        self.width = width
        self.height = height
        self.use_semantic_segmentation = use_semantic_segmentation
        self.export_live2d = export_live2d

        # Character consistency (lazy loaded)
        self._character_card = None
        self._character_manager = None
        if character_consistency is not None:
            self._init_character_consistency(character_consistency)

        # Initialize components
        self.router = get_router(config)
        self.qa_engine = QAEngine()
        self.kmeans_layerer = KMeansLayerer(k_clusters=k_clusters)
        self.semantic_layerer = SemanticLayerer()
        self.layer_composer = LayerComposer()
        self.layer52_gen = Layer52Generator()
        self.psd_creator = PSDCreator()
        self.part_identifier = PartIdentifier()

        # Workflow state
        self.state = "idle"
        self._temp_files = []  # P1-2: track temp files for cleanup
        self._progress_cb: Optional[Callable[[str, str, float], None]] = None

    # ------------------------------------------------------------------
    # Character consistency helpers
    # ------------------------------------------------------------------

    def _init_character_consistency(self, character_consistency: Any) -> None:
        """Resolve a CharacterCard or character_id into self._character_card."""
        from core.character.card import CharacterCard
        from core.character.manager import CharacterManager

        if isinstance(character_consistency, CharacterCard):
            self._character_card = character_consistency
            return

        if isinstance(character_consistency, str):
            # character_id string - lazy load on first run
            self._character_manager = CharacterManager()
            try:
                self._character_card = self._character_manager.load_character(character_consistency)
                log.info(f"Character consistency loaded: {self._character_card.name}")
            except FileNotFoundError:
                log.warning(f"Character not found: {character_consistency}, creating new")
                self._character_card = self._character_manager.create_character(
                    name=character_consistency
                )

    @property
    def character_card(self) -> Any:
        """Return the current CharacterCard, or None."""
        return self._character_card

    def set_progress_callback(self, cb: Callable[[str, str, float], None]) -> None:
        """Set progress callback: cb(state, message, percent_0_100)."""
        self._progress_cb = cb

    def _set_state(self, new_state: str, message: str = "", percent: float = 0) -> None:
        self.state = new_state
        try:
            log.step(self.STATES.index(new_state) + 1, len(self.STATES), message)
        except (ValueError, AttributeError):
            log.info(f"[{new_state}] {message}")
        if self._progress_cb:
            self._progress_cb(new_state, message, percent)

    def _track_temp(self, filepath: str) -> None:
        """P1-2: Track a temporary file for cleanup."""
        self._temp_files.append(filepath)

    def _cleanup_temp(self) -> None:
        """P1-2: Remove temporary files after workflow completion or failure."""
        for fpath in self._temp_files:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
                    log.debug(f"Cleaned temp file: {fpath}")
            except OSError as e:
                log.debug(f"Failed to clean temp file {fpath}: {e}")
        self._temp_files.clear()

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(
        self,
        prompt: str = "",
        input_image: Optional[str] = None,
        deploy_desktop: bool = False,
        auto_fallback: bool = True,
        generate_52_config: bool = True,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run the complete Live2D creation pipeline.

        Args:
            prompt: Text description for image generation (if no input_image)
            input_image: Path to existing character image (skip generation)
            deploy_desktop: Whether to create desktop pet package
            auto_fallback: Auto-fallback between providers/libraries
            generate_52_config: Generate 52-layer standard config files
            negative_prompt: Negative prompt for generation
            seed: Random seed for reproducibility

        Returns:
            Result dict with paths and status info
        """
        result: Dict[str, Any] = {"version": __version__, "success": False, "steps": {}}
        temp_generated = None

        # Validate inputs before the pipeline try/except
        if not input_image and not prompt:
            raise RuntimeError("Either prompt or input_image must be provided")
        if input_image:
            valid, reason = validate_image_path(input_image)
            if not valid:
                raise RuntimeError(f"Invalid input image: {reason}")

        # Merge character consistency prompts
        effective_prompt = prompt
        effective_negative = negative_prompt or ""
        if self._character_card:
            style_suffix = self._character_card.generate_style_prompt()
            if effective_prompt:
                effective_prompt = f"{effective_prompt}, {style_suffix}"
            else:
                effective_prompt = style_suffix
            char_neg = self._character_card.generate_negative_prompt()
            if effective_negative:
                effective_negative = f"{effective_negative}, {char_neg}"
            else:
                effective_negative = char_neg
            log.info(f"Character consistency applied: {self._character_card.name}")

        try:
            log.section(f"Live2D Master Agent v{__version__}")

            # === Step 1: Generate or load image ===
            if input_image:
                self._set_state("generating", f"Loading input image: {input_image}", 5)
                character_path = input_image
                log.info(f"Using provided image: {input_image}")
            else:
                self._set_state("generating", f"Generating character: {effective_prompt[:60]}...", 5)
                safe_prompt = sanitize_prompt(effective_prompt)
                timestamp = int(time.time())
                gen_output = str(self.output_dir / f"character_{timestamp}.png")
                # NOTE: gen_output is a final deliverable (the raw generated
                # character image) — do NOT track it as temp; user may want it.

                gen_result = self.router.generate(
                    prompt=safe_prompt,
                    output_path=gen_output,
                    width=self.width,
                    height=self.height,
                    provider=self.provider_name,
                    negative_prompt=effective_negative or None,
                    seed=seed,
                    auto_fallback=auto_fallback,
                )
                character_path = gen_result.image_path
                temp_generated = character_path
                result["steps"]["generate"] = {
                    "provider": gen_result.provider,
                    "model": gen_result.model,
                    "path": character_path,
                    "elapsed": gen_result.elapsed_seconds,
                }
                log.success(f"Image generated: {character_path}")

            # Load the character image
            character_img = Image.open(character_path).convert('RGBA')
            log.info(f"Image size: {character_img.size}")

            # === Step 2: Quality assessment ===
            self._set_state("qa_check", "Running quality assessment", 25)
            qa_result = self.qa_engine.assess_image(character_img)
            result["steps"]["qa"] = {
                "score": qa_result.score,
                "valid": qa_result.valid,
                "issues": [i.to_dict() for i in qa_result.issues],
            }
            log.info(f"Quality score: {qa_result.score}/100 (valid={qa_result.valid})")
            for issue in qa_result.issues:
                if issue.severity.value == "error":
                    log.warning(f"  [ERROR] {issue.message}")
                elif issue.severity.value == "warning":
                    log.warning(f"  [WARN]  {issue.message}")

            # === Step 3: Image optimization ===
            self._set_state("optimizing", "Optimizing image (background removal, enhancement)", 45)
            optimized_img = self._optimize_image(character_img)
            # Save optimized version (output artifact, not temp)
            timestamp = int(time.time())
            optimized_path = str(self.output_dir / f"optimized_{timestamp}.png")
            optimized_img.save(optimized_path)
            result["steps"]["optimize"] = {"path": optimized_path}
            log.info("Image optimized: background removed, contrast enhanced")

            # === Step 4: Layer separation ===
            if self.use_semantic_segmentation:
                self._set_state("layering", "Semantic segmentation (with K-means fallback)", 60)
                layers_output = str(self.output_dir / f"layers_{timestamp}")
                layer_result = self.semantic_layerer.layer(optimized_img, output_dir=layers_output)
                # v10.1: fall back to KMeans when semantic is unavailable OR when
                # it degraded to HSV color fallback (quality ≈ KMeans, but KMeans
                # produces more coherent clusters for rigging). Use a _kmeans suffixed
                # directory so the semantic output remains available for diagnosis
                # without mixing with KMeans layers.
                if not layer_result.get("success") or layer_result.get("method") == "semantic_hsv_fallback":
                    reason = "unavailable" if not layer_result.get("success") else "HSV fallback"
                    log.info(f"Semantic segmentation {reason}; falling back to K-means")
                    layers_output_k = str(self.output_dir / f"layers_{timestamp}_kmeans")
                    layer_result = self.kmeans_layerer.layer(optimized_img, output_dir=layers_output_k)
            else:
                self._set_state("layering", f"K-means layering (k={self.k_clusters})", 60)
                layers_output = str(self.output_dir / f"layers_{timestamp}")
                layer_result = self.kmeans_layerer.layer(optimized_img, output_dir=layers_output)

            # Extract timestamp/output dir from layer_result
            layers_output = layer_result.get("output_dir", str(self.output_dir / f"layers_{timestamp}"))

            # Identify parts
            layers_with_parts = self.part_identifier.identify_layers(
                layer_result["layers"], optimized_img.height, optimized_img.width
            )
            result["steps"]["layering"] = {
                "output_dir": layers_output,
                "layer_count": layer_result["layer_count"],
                "preview": layer_result.get("preview_path"),
                "composite_preview": layer_result.get("composite_preview"),
                "method": layer_result.get("method", "kmeans"),
            }
            log.success(f"Layering complete: {layer_result['layer_count']} layers "
                        f"({layer_result.get('method', 'kmeans')})")

            # === Step 4b: Automatic rigging + Live2D model3 export ===
            # RiggingPipeline internally runs Live2DBuilder.build() which calls
            # Model3Exporter.export(), producing a complete model3.json bundle.
            # The separate Step 6b live2d_export in earlier versions redundantly
            # called Model3Exporter a second time with wrong args (raw layers
            # instead of builder_result), producing an empty model. Removed in v10.1.
            should_rig = generate_52_config or self.export_live2d
            rig_result: Optional[Dict[str, Any]] = None
            if should_rig:
                self._set_state("rigging", "Automatic rigging & Live2D export", 70)
                from collections import OrderedDict
                from live2d_builder.pipeline import RiggingPipeline
                layers_for_rig = OrderedDict()
                used_names: Dict[str, int] = {}
                for info in layer_result["layers"]:
                    path = info["path"]
                    name = self._rigging_layer_name(info, path, used_names)
                    try:
                        layers_for_rig[name] = Image.open(path).convert("RGBA")
                    except Exception as exc:
                        log.warning(f"Skipping unreadable layer '{name}': {exc}")
                rig_output = str(self.output_dir / f"rigged_{timestamp}")
                rig_result = RiggingPipeline().run(
                    layers_for_rig,
                    output_dir=rig_output,
                    character_name=(self._character_card.name if self._character_card
                                    else "generated_character"),
                )
                if not isinstance(rig_result, dict):
                    rig_result = {}
                model3_path = self._extract_model3_path(rig_result, rig_output)
                result["steps"]["rigging"] = {
                    "output_dir": rig_output,
                    "model3_json": model3_path,
                    "textures": rig_result.get("textures", []),
                    "texture": (rig_result.get("textures") or [""])[0] if rig_result.get("textures") else "",
                    "physics": rig_result.get("physics"),
                    "expressions": rig_result.get("expressions", []),
                    "validation": rig_result.get("validation", {}).get("valid", False)
                    if isinstance(rig_result.get("validation"), dict) else False,
                    "compatibility": {
                        k: v.get("compatible") if isinstance(v, dict) else None
                        for k, v in rig_result.get("compatibility", {}).items()
                    },
                }
                log.success(f"Rigging complete: {model3_path or 'N/A'}")

            # === Step 5: PSD export ===
            self._set_state("psd_export", "Creating PSD file", 75)
            psd_output = str(Path(layers_output) / "character.psd")
            psd_result = self.psd_creator.create_psd(layers_output, psd_output)
            result["steps"]["psd"] = psd_result
            log.success(f"PSD created: {psd_result.get('psd_path', 'N/A')}")

            # === Step 6: 52-layer mapping (DEF-004) ===
            if generate_52_config:
                self._set_state("mapping", "Generating 52-layer standard config", 85)
                mapping = self.layer52_gen.map_layers_to_standard(layers_with_parts)
                configs = self.layer52_gen.generate_config_files(
                    mapping, layers_output, character_name=(
                        self._character_card.name if self._character_card
                        else "generated_character"
                    )
                )
                result["steps"]["layer52"] = {
                    "mapping": configs.get("layer_mapping"),
                    "parameters": configs.get("parameters"),
                    "physics": configs.get("physics"),
                    "guide": configs.get("guide"),
                    "mapped_count": mapping["mapped_layers"],
                    "missing_required": len(mapping["missing_required"]),
                }
                log.info(f"52-layer config: {mapping['mapped_layers']}/52 mapped")

            # Step 6b (redundant live2d_export) removed in v10.1:
            # RiggingPipeline already exports a complete, valid model3 bundle.

            # === Step 7: Desktop pet (optional) ===
            if deploy_desktop:
                self._set_state("pet_deploy", "Creating desktop pet package", 95)
                pet_result = self._create_pet(rig_output if rig_result else layers_output)
                result["steps"]["pet"] = pet_result

            # === Step 8: Save/update character card ===
            if self._character_card and self._character_manager:
                try:
                    self._character_card.front_view_path = character_path
                    self._character_manager.save_character(self._character_card)
                    result["character_id"] = self._character_card.character_id
                    log.info(f"Character card updated: {self._character_card.character_id}")
                except Exception as e:
                    log.warning(f"Failed to update character card: {e}")

            self._set_state("done", "Workflow complete!", 100)
            result["success"] = True
            result["character_image"] = character_path
            result["layers_dir"] = layers_output
            result["output_dir"] = str(self.output_dir)

        except Exception as e:
            self._set_state("error", f"Error: {e}", 0)
            log.error(f"Workflow failed: {e}", exc_info=True)
            result["success"] = False
            result["error"] = str(e)
            result["error_state"] = self.state
        finally:
            # P1-2: Clean up temporary files (but keep final outputs)
            self._cleanup_temp()

        log.section("Workflow Summary")
        if result["success"]:
            log.success(f"Pipeline complete! Output: {result.get('layers_dir', '')}")
        else:
            log.error(f"Pipeline failed at state '{result.get('error_state', '?')}': {result.get('error', '')}")

        return result

    def _optimize_image(self, img: Image.Image) -> Image.Image:
        """Optimize image for Live2D layering:
        - Background removal (threshold or rembg)
        - Contrast enhancement
        - Sharpness enhancement
        - Mild color quantization
        """
        result = img.copy()

        # Background removal (simple threshold on corners)
        if result.mode == 'RGBA':
            data = np.array(result)
            r, g, b, a = data[:,:,0], data[:,:,1], data[:,:,2], data[:,:,3]
            # Sample corner pixels to detect background color
            corners = np.vstack([
                data[:20, :20, :3].reshape(-1, 3),
                data[:20, -20:, :3].reshape(-1, 3),
                data[-20:, :20, :3].reshape(-1, 3),
                data[-20:, -20:, :3].reshape(-1, 3),
            ])
            bg_color = np.median(corners, axis=0)

            # Create alpha mask from color distance to background
            rgb = data[:,:,:3].astype(float)
            dist = np.sqrt(((rgb - bg_color) ** 2).sum(axis=2))
            # Pixels very close to bg color become transparent; smooth ramp at boundary
            alpha_ramp = (255.0 * (dist - 25) / 25.0)
            alpha_ramp = np.clip(alpha_ramp, 0, 255)
            alpha_new = np.where(dist < 25, 0, np.where(dist < 50, alpha_ramp, 255)).astype(np.uint8)
            # Also respect existing alpha
            data[:,:,3] = np.minimum(a, alpha_new)
            result = Image.fromarray(data, 'RGBA')

        # Contrast enhancement
        enhancer = ImageEnhance.Contrast(result)
        result = enhancer.enhance(1.3)

        # Sharpness enhancement
        enhancer = ImageEnhance.Sharpness(result)
        result = enhancer.enhance(1.8)

        # Color quantization (mild - reduce to help K-means)
        result = result.convert('P', palette=Image.Palette.ADAPTIVE, colors=64).convert('RGBA')

        return result

    @staticmethod
    def _rigging_layer_name(
        info: Dict[str, Any],
        path: str,
        used: Dict[str, int],
    ) -> str:
        """Derive a semantically meaningful, unique rigging layer name.

        Generic ``layer_NNN`` names are replaced with the part identifier
        (``part_name_en``) so the bone/deformer/part-group systems can match
        hair/eyes/mouth/etc. Eye/brow parts are disambiguated into left/right
        by their x-centroid, and duplicate names receive a numeric suffix.
        """
        import re

        part = (info.get("part_name_en") or "").strip().lower()
        fallback = info.get("name") or Path(path).stem
        if not part or part in ("unknown", "未分类"):
            base = fallback
        else:
            cx = float(info.get("centroid_x_ratio", 0.5))
            side = "left" if cx < 0.45 else ("right" if cx > 0.55 else "")
            mapping = {
                "hair": "hair_back",
                "hair_highlight": "hair_front",
                "hair_back": "hair_back",
                "hair_front": "hair_front",
                "skin": "face_base",
                "face": "face_base",
                "blush": "cheek",
                "nose": "nose",
                "mouth": "mouth",
                "clothes": "clothes",
                "clothes_shadow": "clothes",
                "shadow": "hair_back",
                "eye_white": f"eye_{side}" if side else "eye_left",
                "eye_iris": f"eyeball_{side[0]}" if side else "eyeball_l",
                "eyebrows": f"brow_{side[0]}" if side else "brow_l",
            }
            base = mapping.get(part, re.sub(r"[^a-z0-9_]", "_", part))

        # Guarantee uniqueness while preserving the semantic keyword.
        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 0
        return base

    @staticmethod
    def _extract_model3_path(rig_result: Any, rig_output: str) -> Optional[str]:
        """Resolve a usable ``.model3.json`` path from a rigging result.

        Tolerates the historical variety of return shapes:
        - dict with ``model3_json`` / ``model_json`` / ``model3_path``;
        - a bare string path;
        - otherwise scan ``build_meta``'s directory (then ``rig_output``)
          for the first ``.model3.json`` file.

        Returns None only when nothing can be found (callers treat this as
        a non-fatal degradation).
        """
        def _is_m3(v: Any) -> bool:
            return isinstance(v, str) and v.endswith(".model3.json")

        if isinstance(rig_result, dict):
            for key in ("model3_json", "model_json", "model3_path"):
                candidate = rig_result.get(key)
                if _is_m3(candidate):
                    return candidate
            # Fall back to scanning the build_meta / output directory.
            search_dirs = []
            build_meta = rig_result.get("build_meta")
            if isinstance(build_meta, str):
                search_dirs.append(str(Path(build_meta).parent))
            if rig_output:
                search_dirs.append(rig_output)
            for base in search_dirs:
                try:
                    matches = sorted(Path(base).glob("*.model3.json"))
                except OSError:
                    matches = []
                if matches:
                    return str(matches[0])
            return None

        if isinstance(rig_result, str):
            return rig_result if _is_m3(rig_result) else None

        return None

    def _create_pet(self, layers_dir: str) -> Dict:
        """Create desktop pet package."""
        try:
            animator = DesktopPetAnimator(layers_dir)
            animator.load_layers()
            pet_output = str(Path(layers_dir).parent / "pet_packages")
            return animator.create_pet_package(pet_output)
        except Exception as e:
            log.warning(f"Pet creation failed (non-fatal): {e}")
            return {"success": False, "error": str(e)}


# Convenience function
def run_workflow(
    prompt: str = "",
    input_image: Optional[str] = None,
    output_dir: Optional[str] = None,
    deploy_desktop: bool = False,
    k_clusters: int = 12,
    provider: Optional[str] = None,
    character_id: Optional[str] = None,
    use_semantic: bool = True,
    export_live2d: bool = False,
    **kwargs
) -> Dict:
    """Run the Live2D workflow with default settings.

    Args:
        prompt: Character description prompt.
        input_image: Path to input image (skip generation).
        output_dir: Output directory.
        deploy_desktop: Create desktop pet package.
        k_clusters: K-means cluster count.
        provider: Image provider name.
        character_id: Character ID for consistency (loads or creates).
        use_semantic: Use semantic segmentation (True) or K-means (False).
        export_live2d: Export Live2D model3 scaffold.
        **kwargs: Additional arguments passed to WorkflowEngine.run().

    Returns:
        Result dict from the workflow.
    """
    engine = WorkflowEngine(
        output_dir=output_dir,
        k_clusters=k_clusters,
        provider=provider,
        character_consistency=character_id,
        use_semantic_segmentation=use_semantic,
        export_live2d=export_live2d,
    )
    return engine.run(
        prompt=prompt,
        input_image=input_image,
        deploy_desktop=deploy_desktop,
        **kwargs,
    )


if __name__ == "__main__":
    import json as _json
    import argparse
    parser = argparse.ArgumentParser(description=f"Live2D Master Agent v{__version__}")
    parser.add_argument("prompt", nargs="?", default="", help="Character description")
    parser.add_argument("--input", "-i", help="Input image path (skip generation)")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--deploy-desktop", action="store_true", help="Create desktop pet")
    parser.add_argument("--k", type=int, default=12, help="K-means clusters (default: 12)")
    parser.add_argument("--provider", choices=["pollinations", "sensenova", "seedream", "auto"],
                        default="auto", help="Image provider")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--version", "-V", action="version", version=FULL_VERSION_STRING)
    # v10.0 new options
    parser.add_argument("--character-id", help="Character ID for consistency (loads or creates)")
    parser.add_argument("--semantic", dest="semantic", action="store_true", default=True,
                        help="Use semantic segmentation (default)")
    parser.add_argument("--no-semantic", dest="semantic", action="store_false",
                        help="Disable semantic segmentation, use K-means")
    parser.add_argument("--live2d-export", action="store_true",
                        help="Export Live2D model3 scaffold after rigging")
    # v10.1 new options for API integration
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--negative-prompt", "-n", default=None, help="Negative prompt")
    parser.add_argument("--json", action="store_true", help="Output result as JSON to stdout")
    args = parser.parse_args()

    if not args.prompt and not args.input:
        parser.print_help()
        sys.exit(1)

    # In --json mode, route all logging to stderr so stdout carries only the
    # JSON payload (also silences third-party banners such as pygame's).
    if args.json:
        os.environ["LIVE2D_JSON_MODE"] = "1"
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

    wf = WorkflowEngine(
        output_dir=args.output,
        k_clusters=args.k,
        provider=None if args.provider == "auto" else args.provider,
        width=args.width,
        height=args.height,
        character_consistency=args.character_id,
        use_semantic_segmentation=args.semantic,
        export_live2d=args.live2d_export,
    )
    result = wf.run(
        prompt=args.prompt,
        input_image=args.input,
        deploy_desktop=args.deploy_desktop,
        negative_prompt=args.negative_prompt,
        seed=args.seed,
    )
    if args.json:
        print(_json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["success"] else 1)
