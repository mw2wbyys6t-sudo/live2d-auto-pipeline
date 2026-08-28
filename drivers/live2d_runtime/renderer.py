#!/usr/bin/env python3
"""
Software Live2D-like renderer.

This is NOT a full Cubism renderer. It provides Live2D-like motion by:
1. Loading ordered PNG layers (textures) from a model directory
2. Applying parameter-driven transforms (translation, rotation, scale)
   per layer group
3. Supporting simple warp deformers
4. Simulating physics (hair swing, breathing), eye blink, and lip-sync
5. Compositing layers with per-pixel alpha

Works with:
- Standard Live2D model3.json (parses texture references and parameter list)
- Plain directories of ordered PNG layers (layer_00.png, layer_01.png, ...)
"""

import json
import math
import time
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from core.logger import get_logger

log = get_logger("live2d_renderer")

try:
    from PIL import Image, ImageTransform
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    Image = None  # type: ignore
    ImageTransform = None  # type: ignore

try:
    import numpy as np
    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False
    np = None  # type: ignore

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False
    pygame = None  # type: ignore


# Render order (back to front)
RENDER_ORDER = [
    "background", "shadow", "hair_back", "body", "leg_l", "leg_r",
    "clothes", "arm_l", "arm_r", "neck",
    "face", "ear_l", "ear_r", "nose",
    "eyebrows", "eyes", "eyelashes",
    "mouth", "hair_front", "accessories",
]


class Live2DRenderer:
    """Software Live2D-like renderer using PNG layer compositing.

    Parameters
    ----------
    model_dir : str
        Path to model directory containing model3.json and textures,
        or a directory of ordered PNG layers.
    width, height : int
        Canvas size for rendering.
    """

    def __init__(self, model_dir: str, width: int = 600, height: int = 800):
        self.model_dir = Path(model_dir)
        self.width = width
        self.height = height

        # Loaded data
        self.layers: Dict[str, Image.Image] = {}
        self.layer_order: List[str] = []
        self.layer_groups: Dict[str, str] = {}  # layer_name -> group
        self.deformers: Dict[str, dict] = {}
        self.model3_data: Optional[dict] = None
        self._model_loaded = False

        # Parameters (Live2D standard)
        self.params: Dict[str, float] = {
            "ParamAngleX": 0.0, "ParamAngleY": 0.0, "ParamAngleZ": 0.0,
            "ParamBodyAngleX": 0.0, "ParamBodyAngleY": 0.0,
            "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
            "ParamEyeBallX": 0.0, "ParamEyeBallY": 0.0,
            "ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0,
            "ParamBrowLY": 0.0, "ParamBrowRY": 0.0,
            "ParamBreath": 0.5,
            "ParamCheek": 0.0,
            "ParamHairFrontX": 0.0, "ParamHairBackX": 0.0,
        }
        self._param_defaults: Dict[str, float] = dict(self.params)

        # Physics state
        self._physics_time: float = 0.0
        self._hair_physics: float = 0.0
        self._hair_velocity: float = 0.0
        self._breath_phase: float = 0.0
        self._blink_state: float = 1.0  # 1=open, 0=closed
        self._blink_timer: float = 0.0
        self._next_blink: float = 3.0
        self._lip_sync_value: float = 0.0

        self._last_time = time.time()

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def load_model(self, model3_path: str) -> bool:
        """Load a model3.json or a directory of PNG layers.

        Parameters
        ----------
        model3_path : str
            Path to a .model3.json file, or a directory containing PNG layers.

        Returns
        -------
        bool
            True if the model was loaded successfully.
        """
        if not _PIL_AVAILABLE:
            log.error("Pillow is required for Live2DRenderer. Install: pip install Pillow")
            return False

        p = Path(model3_path)

        if p.is_file() and p.suffix == ".json":
            return self._load_model3(p)
        elif p.is_dir():
            return self._load_layer_dir(p)
        else:
            log.error(f"Invalid model path: {model3_path}")
            return False

    def _load_model3(self, model3_path: Path) -> bool:
        """Parse a Live2D model3.json and load its textures."""
        try:
            self.model3_data = json.loads(model3_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"Failed to parse model3.json: {e}")
            return False

        model_dir = model3_path.parent
        refs = self.model3_data.get("FileReferences", {})

        # Load textures
        textures = refs.get("Textures", [])
        for tex_path in textures:
            full = model_dir / tex_path
            if full.is_file():
                try:
                    img = Image.open(full).convert("RGBA")
                    name = Path(tex_path).stem
                    self.layers[name] = img
                    self.layer_order.append(name)
                    self.layer_groups[name] = self._guess_group(name)
                except Exception as e:
                    log.warning(f"Could not load texture {full}: {e}")

        # If single atlas, also load individual PNGs from directory
        if len(self.layers) <= 1:
            self._load_layer_dir(model_dir)

        # Load parameters from model3
        for param_def in self.model3_data.get("Parameters", []):
            pid = param_def.get("Id", "")
            if pid:
                self.params[pid] = param_def.get("Default", 0.0)
                self._param_defaults[pid] = param_def.get("Default", 0.0)

        # Load deformer info (Groups section)
        for group in self.model3_data.get("Groups", []):
            gname = group.get("Name", "")
            gids = group.get("Ids", [])
            self.deformers[gname] = {"ids": gids}

        self._model_loaded = len(self.layers) > 0
        if self._model_loaded:
            log.success(f"Loaded model with {len(self.layers)} layers")
        else:
            log.warning("No layers loaded from model3.json")
        return self._model_loaded

    def _load_layer_dir(self, directory: Path) -> bool:
        """Load all PNG files from a directory as ordered layers."""
        pngs = sorted(directory.glob("*.png"))
        pngs = [f for f in pngs if f.name not in ("preview.png", "composite_preview.png")]

        for pf in pngs:
            try:
                img = Image.open(pf).convert("RGBA")
                name = pf.stem
                self.layers[name] = img
                self.layer_order.append(name)
                self.layer_groups[name] = self._guess_group(name)
            except Exception as e:
                log.debug(f"Could not load layer {pf}: {e}")

        self._model_loaded = len(self.layers) > 0
        if self._model_loaded:
            log.info(f"Loaded {len(self.layers)} PNG layers from {directory}")
        return self._model_loaded

    @staticmethod
    def _guess_group(name: str) -> str:
        """Guess a layer group from its filename."""
        nl = name.lower()
        guesses = [
            ("hair_back", ["hair_back", "back_hair", "后发"]),
            ("hair_front", ["hair_front", "bangs", "ahoge", "刘海", "前发"]),
            ("eyes", ["eye", "iris", "pupil", "highlight", "眼", "目"]),
            ("eyelashes", ["eyelash", "lash", "睫毛"]),
            ("eyebrows", ["eyebrow", "brow", "眉"]),
            ("mouth", ["mouth", "lip", "teeth", "tongue", "嘴", "口"]),
            ("face", ["face", "skin", "脸", "皮肤"]),
            ("nose", ["nose", "鼻"]),
            ("ear_l", ["ear_l", "left_ear", "左耳"]),
            ("ear_r", ["ear_r", "right_ear", "右耳"]),
            ("body", ["body", "chest", "torso", "neck", "身体", "躯干", "颈"]),
            ("clothes", ["clothes", "dress", "shirt", "衣服"]),
            ("arm_l", ["arm_l", "left_arm", "左臂"]),
            ("arm_r", ["arm_r", "right_arm", "右臂"]),
            ("leg_l", ["leg_l", "left_leg", "左腿"]),
            ("leg_r", ["leg_r", "right_leg", "右腿"]),
            ("shadow", ["shadow", "阴影"]),
            ("accessories", ["accessory", "accessories", "饰品"]),
            ("background", ["background", "bg"]),
        ]
        for group, keywords in guesses:
            for kw in keywords:
                if kw in nl:
                    return group
        return "body"

    # ------------------------------------------------------------------
    # Parameter interface
    # ------------------------------------------------------------------

    def set_parameter(self, name: str, value: float) -> None:
        """Set a single Live2D parameter value."""
        self.params[name] = float(value)

    def set_parameters(self, params: Dict[str, float]) -> None:
        """Set multiple parameters at once."""
        for name, value in params.items():
            self.params[name] = float(value)

    def get_parameter(self, name: str, default: float = 0.0) -> float:
        """Get a parameter value with fallback."""
        return self.params.get(name, default)

    def reset_parameters(self) -> None:
        """Reset all parameters to their default values."""
        self.params = dict(self._param_defaults)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self) -> "Image.Image":
        """Render the current frame as a PIL RGBA Image.

        Applies all parameter transforms, physics, blink, and lip-sync,
        then composites layers in render order.
        """
        if not _PIL_AVAILABLE:
            return Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))

        now = time.time()
        dt = now - self._last_time
        self._last_time = now
        self._physics_time += dt

        # Run automatic systems
        self._apply_physics(dt)
        self._apply_eye_blink()
        self._apply_lip_sync(self._lip_sync_value)
        self._apply_breathing(dt)

        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))

        # Group layers by their group name
        grouped: Dict[str, List[str]] = {}
        for name in self.layer_order:
            g = self.layer_groups.get(name, "body")
            grouped.setdefault(g, []).append(name)

        rendered = set()
        for group in RENDER_ORDER:
            for name in grouped.get(group, []):
                layer_img = self.layers.get(name)
                if layer_img is None:
                    continue
                transformed = self._transform_layer(name, layer_img, group)
                if transformed is not None:
                    px = (self.width - transformed.width) // 2
                    py = (self.height - transformed.height) // 2
                    canvas.alpha_composite(transformed, (px, py))
                rendered.add(name)

        # Render remaining layers
        for name in self.layer_order:
            if name in rendered:
                continue
            layer_img = self.layers.get(name)
            if layer_img:
                px = (self.width - layer_img.width) // 2
                py = (self.height - layer_img.height) // 2
                canvas.alpha_composite(layer_img, (px, py))

        return canvas

    def render_to_surface(self) -> Optional["pygame.Surface"]:
        """Render to a pygame Surface for use with DesktopPetWindow.

        Returns None if pygame is not available.
        """
        if not _PYGAME_AVAILABLE:
            log.warning("pygame not available for render_to_surface")
            return None

        img = self.render()
        raw = img.tobytes("raw", "RGBA")
        surf = pygame.image.fromstring(raw, img.size, "RGBA")
        return surf

    # ------------------------------------------------------------------
    # Layer transforms
    # ------------------------------------------------------------------

    def _transform_layer(
        self, name: str, img: "Image.Image", group: str
    ) -> Optional["Image.Image"]:
        """Apply parameter-driven transforms to a single layer."""
        result = img.copy()

        angle_deg = 0.0
        offset_x = 0.0
        offset_y = 0.0
        scale_x = 1.0
        scale_y = 1.0

        angle_x = self.get_parameter("ParamAngleX")
        angle_y = self.get_parameter("ParamAngleY")
        angle_z = self.get_parameter("ParamAngleZ")
        body_angle_x = self.get_parameter("ParamBodyAngleX")
        breath = self.get_parameter("ParamBreath", 0.5)

        # Head groups follow head rotation
        if group in ("head", "face", "eyes", "eyebrows", "nose",
                      "ear_l", "ear_r", "hair_front", "eyelashes"):
            angle_deg += angle_z * 0.3
            offset_x += angle_y * 1.5
            offset_y += angle_x * 1.5

        # Hair physics
        if group in ("hair_back", "hair_front"):
            hair_param = self.get_parameter(
                "ParamHairFrontX" if group == "hair_front" else "ParamHairBackX",
                self._hair_physics,
            )
            offset_x += hair_param * 2.0
            angle_deg += hair_param * 0.3

        # Body groups
        if group in ("body", "clothes", "arm_l", "arm_r", "leg_l", "leg_r"):
            angle_deg += body_angle_x * 0.2
            offset_x += body_angle_x * 1.0

        # Breathing offset
        breath_offset = (breath - 0.5) * 4.0
        if group in ("body", "clothes", "hair_back", "hair_front"):
            offset_y += breath_offset * 0.5

        # Eye open/close
        if group == "eyes":
            eye_open_l = self.get_parameter("ParamEyeLOpen", 1.0)
            eye_open_r = self.get_parameter("ParamEyeROpen", 1.0)
            nl = name.lower()
            if "left" in nl or "_l" in nl or "左" in nl:
                scale_y = max(0.05, eye_open_l)
            elif "right" in nl or "_r" in nl or "右" in nl:
                scale_y = max(0.05, eye_open_r)
            else:
                scale_y = max(0.05, min(eye_open_l, eye_open_r))

            eye_ball_x = self.get_parameter("ParamEyeBallX")
            eye_ball_y = self.get_parameter("ParamEyeBallY")
            offset_x += eye_ball_x * 3.0
            offset_y += eye_ball_y * 3.0

            if self._blink_state < 0.95:
                scale_y = min(scale_y, self._blink_state)

        # Mouth open/form
        if group == "mouth":
            mouth_open = self.get_parameter("ParamMouthOpenY")
            mouth_form = self.get_parameter("ParamMouthForm")
            scale_y = 1.0 + mouth_open * 1.5
            scale_x = 1.0 + mouth_form * 0.2

        # Apply warp deformer if defined for this layer
        if name in self.deformers:
            result = self._apply_deformer(result, self.deformers[name], self.params)

        # Apply scale
        if abs(scale_x - 1.0) > 0.001 or abs(scale_y - 1.0) > 0.001:
            new_w = max(1, int(result.width * abs(scale_x)))
            new_h = max(1, int(result.height * abs(scale_y)))
            result = result.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Apply rotation
        if abs(angle_deg) > 0.1:
            result = result.rotate(angle_deg, resample=Image.Resampling.BICUBIC, expand=True)

        # Apply offset by expanding canvas
        if abs(offset_x) > 0.1 or abs(offset_y) > 0.1:
            pad_x = int(abs(offset_x)) + 5
            pad_y = int(abs(offset_y)) + 5
            new_img = Image.new("RGBA",
                                (result.width + pad_x * 2, result.height + pad_y * 2),
                                (0, 0, 0, 0))
            paste_x = pad_x + int(offset_x)
            paste_y = pad_y + int(offset_y)
            new_img.alpha_composite(result, (max(0, paste_x), max(0, paste_y)))
            result = new_img

        return result

    def _apply_deformer(
        self, layer_img: "Image.Image", deformer: dict, param_values: dict
    ) -> "Image.Image":
        """Apply a warp deformer to a layer image.

        Simplified mesh warp using PIL perspective transform. The deformer
        dict specifies corner offsets driven by parameter values.
        """
        try:
            w, h = layer_img.size
            offsets = deformer.get("offsets", {})
            if not offsets:
                return layer_img

            tl_x = sum(param_values.get(p, 0) * s for p, s in offsets.get("tl_x", []))
            tl_y = sum(param_values.get(p, 0) * s for p, s in offsets.get("tl_y", []))
            tr_x = sum(param_values.get(p, 0) * s for p, s in offsets.get("tr_x", []))
            tr_y = sum(param_values.get(p, 0) * s for p, s in offsets.get("tr_y", []))
            bl_x = sum(param_values.get(p, 0) * s for p, s in offsets.get("bl_x", []))
            bl_y = sum(param_values.get(p, 0) * s for p, s in offsets.get("bl_y", []))
            br_x = sum(param_values.get(p, 0) * s for p, s in offsets.get("br_x", []))
            br_y = sum(param_values.get(p, 0) * s for p, s in offsets.get("br_y", []))

            src_quad = [(0, 0), (w, 0), (w, h), (0, h)]
            dst_quad = [
                (tl_x, tl_y), (w + tr_x, tr_y),
                (w + br_x, h + br_y), (bl_x, h + bl_y),
            ]

            coeffs = self._compute_perspective_coeffs(src_quad, dst_quad)
            if coeffs:
                return layer_img.transform(
                    (w, h), ImageTransform.PERSPECTIVE, coeffs,
                    Image.Resampling.BICUBIC,
                )
        except Exception as e:
            log.debug(f"Deformer error: {e}")

        return layer_img

    @staticmethod
    def _compute_perspective_coeffs(
        src: List[Tuple[float, float]], dst: List[Tuple[float, float]]
    ) -> Optional[List[float]]:
        """Compute perspective transform coefficients from quad mapping."""
        if not _NP_AVAILABLE:
            return None
        try:
            matrix = []
            for (x, y), (u, v) in zip(src, dst):
                matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y, u])
                matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y, v])
            m = np.array(matrix, dtype=np.float64)
            _, _, vh = np.linalg.svd(m)
            coeffs = vh[-1, :] / vh[-1, -1]
            return coeffs[:8].tolist()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Automatic animation systems
    # ------------------------------------------------------------------

    def _apply_physics(self, delta_time: float) -> None:
        """Update physics simulation (hair spring physics)."""
        target_hair = self.get_parameter("ParamAngleY", 0.0) * 0.5
        spring = 8.0
        damping = 0.85

        error = target_hair - self._hair_physics
        self._hair_velocity += error * spring * max(delta_time, 0.001)
        self._hair_velocity *= damping
        self._hair_physics += self._hair_velocity * max(delta_time, 0.001)

    def _apply_eye_blink(self) -> None:
        """Automatic eye blink simulation."""
        self._blink_timer += 0.016

        if self._blink_timer >= self._next_blink:
            self._blink_state = max(0.0, self._blink_state - 0.3)
            if self._blink_state <= 0.01:
                self._next_blink = self._blink_timer + random.uniform(3.0, 6.0)
        elif self._blink_state < 1.0:
            self._blink_state = min(1.0, self._blink_state + 0.2)

    def _apply_lip_sync(self, mouth_open: float) -> None:
        """Apply lip-sync mouth open value (set via set_lip_sync)."""
        if mouth_open > 0.01:
            self.params["ParamMouthOpenY"] = max(
                self.params.get("ParamMouthOpenY", 0.0), mouth_open
            )

    def set_lip_sync(self, value: float) -> None:
        """Set lip-sync mouth open value from external audio analysis."""
        self._lip_sync_value = max(0.0, min(1.0, value))

    def _apply_breathing(self, delta_time: float) -> None:
        """Sinusoidal breathing animation on ParamBreath."""
        self._breath_phase += delta_time * 0.8
        if abs(self.params.get("ParamBreath", 0.5) - 0.5) < 0.02:
            self.params["ParamBreath"] = 0.5 + math.sin(self._breath_phase) * 0.15
