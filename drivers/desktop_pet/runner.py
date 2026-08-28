#!/usr/bin/env python3
"""
Live2D Master Agent - Pet Runner

Runs a pet package or layer directory directly. Supports two modes:
1. Real-time parameter-driven mode with FaceTracker + AudioCapture
2. Pre-rendered frame mode as fallback (from frames/ directory)

Uses proper project logging via core.logger.
"""

import os
import sys
import math
import random
import time
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

from core.logger import get_logger

log = get_logger("pet_runner")

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False
    pygame = None  # type: ignore


class PetRunner:
    """Run a desktop pet from a layers directory or pet package.

    Parameters
    ----------
    layers_dir : str
        Path to directory containing layer PNGs or a pet package.
    width, height : int
        Window size (auto-detected from layers if not specified).
    fps : int
        Target frames per second.
    enable_tracking : bool
        If True, attempt to enable real-time face tracking + lip-sync.
    """

    def __init__(
        self,
        layers_dir: str,
        width: int = 256,
        height: int = 384,
        fps: int = 60,
        enable_tracking: bool = False,
    ):
        self.layers_dir = Path(layers_dir)
        self.width = width
        self.height = height
        self.fps = fps
        self.enable_tracking = enable_tracking
        self._running = False

        # Tracking components (lazy)
        self._face_tracker = None
        self._audio_capture = None
        self._blendshape_mapper = None
        self._track_params: Dict[str, float] = {}

    def run(self) -> bool:
        """Run the pet window (blocking). Returns True on clean exit."""
        if not _PYGAME_AVAILABLE:
            log.error("pygame is required. Install: pip install pygame")
            return False

        pygame.init()

        # Check for pre-rendered frames (fallback mode)
        frames_dir = self.layers_dir / "frames"
        layers = self._load_layers(pygame)

        # If a pet package, look for layers subdirectory
        pkg_layers = self.layers_dir / "layers"
        if not layers and pkg_layers.is_dir():
            layers = self._load_layers_from(pkg_layers, pygame)

        pre_rendered_frames: List[Any] = []
        if frames_dir.is_dir():
            pre_rendered_frames = self._load_frames(frames_dir, pygame)

        if not layers and not pre_rendered_frames:
            log.warning("No layers or frames found, using default pet")
            layers = [self._default_layer(pygame)]

        # Auto-detect canvas size from layers
        if layers:
            w, h = layers[0][1].get_size()
            self.width, self.height = w, h

        # Initialize tracking if requested
        if self.enable_tracking:
            self._init_tracking()

        screen = pygame.display.set_mode(
            (self.width, self.height), pygame.NOFRAME | pygame.SRCALPHA
        )
        pygame.display.set_caption("Live2D Pet")
        clock = pygame.time.Clock()

        # Initial position (bottom-right area)
        info = pygame.display.Info()
        pet_x = info.current_w - self.width - 50
        pet_y = info.current_h - self.height - 80

        start = time.time()
        next_blink = start + random.uniform(3, 6)
        blink_end = 0
        dragging = False
        drag_off = (0, 0)
        next_move = start + 10
        expression = "normal"
        next_expr = start + 8

        # Pre-rendered frame mode
        frame_idx = 0

        self._running = True
        while self._running:
            now = time.time()
            t = now - start
            clock.tick(self.fps)

            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self._running = False
                elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    self._running = False
                elif ev.type == pygame.MOUSEBUTTONDOWN:
                    if ev.button == 1:
                        dragging = True
                        mx, my = ev.pos
                        drag_off = (mx, my)
                        expression = "happy"
                        next_expr = now + 2
                    elif ev.button == 3:
                        self._running = False
                elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                    dragging = False
                elif ev.type == pygame.MOUSEMOTION and dragging:
                    mx, my = ev.pos
                    pet_x = mx - drag_off[0]
                    pet_y = my - drag_off[1]

            # Auto-move
            if now > next_move and not dragging:
                info2 = pygame.display.Info()
                pet_x = random.randint(20, max(20, info2.current_w - self.width - 20))
                next_move = now + random.uniform(8, 15)

            # --- Real-time tracking update ---
            track_eye_open = 1.0
            track_mouth = 0.0
            track_angle_x = 0.0
            track_angle_y = 0.0
            if self._face_tracker and self._face_tracker.is_running():
                try:
                    lms = self._face_tracker.get_landmarks()
                    if lms:
                        bs = self._face_tracker.get_blendshapes()
                        rot = self._face_tracker.get_head_rotation()
                        lm_data = dict(lms)
                        if rot:
                            lm_data["head_rotation"] = {
                                "pitch": rot["x"], "yaw": rot["y"], "roll": rot["z"]
                            }
                        if self._blendshape_mapper:
                            self._track_params = self._blendshape_mapper.map_to_live2d_params(
                                bs, lm_data
                            )
                            track_eye_open = min(
                                self._track_params.get("ParamEyeLOpen", 1.0),
                                self._track_params.get("ParamEyeROpen", 1.0),
                            )
                            track_angle_x = self._track_params.get("ParamAngleX", 0)
                            track_angle_y = self._track_params.get("ParamAngleY", 0)
                except Exception as e:
                    log.debug(f"Tracking update error: {e}")

            if self._audio_capture and self._audio_capture.is_running():
                try:
                    track_mouth = self._audio_capture.get_mouth_open_amount()
                except Exception:
                    pass

            # Blink
            eye_open = track_eye_open
            if not (self._face_tracker and self._face_tracker.is_running()):
                if now < blink_end:
                    eye_open = max(0, (blink_end - now) / 0.15)
                elif now > next_blink:
                    blink_end = now + 0.15
                    next_blink = now + random.uniform(3, 6)

            # Expression
            if now > next_expr and not (self._face_tracker and self._face_tracker.is_running()):
                expression = random.choice(["normal", "happy", "shy", "normal"])
                next_expr = now + random.uniform(6, 12)

            # Render
            screen.fill((0, 0, 0, 0))

            if pre_rendered_frames:
                # Fallback: pre-rendered frame animation
                frame_idx = (frame_idx + 1) % len(pre_rendered_frames)
                frame = pygame.transform.smoothscale(
                    pre_rendered_frames[frame_idx], (self.width, self.height)
                )
                screen.blit(frame, (pet_x, pet_y))
            else:
                # Parameter-driven layer compositing
                swing = math.sin(t * 0.8) * 3.0
                breath = math.sin(t * 0.4) * 1.5
                hair_off = swing * 1.8

                for name, surf in layers:
                    nl = name.lower()
                    ox = breath + track_angle_y * 0.3
                    oy = 0.0 + track_angle_x * 0.2

                    if any(k in nl for k in ["hair", "bangs"]):
                        ox += hair_off
                    elif any(k in nl for k in ["body", "clothes"]):
                        ox += swing * 0.5

                    draw_surf = surf
                    if eye_open < 0.9 and any(k in nl for k in ["eye", "iris", "pupil"]):
                        sw, sh = surf.get_size()
                        nh = max(1, int(sh * eye_open))
                        draw_surf = pygame.transform.scale(surf, (sw, nh))
                        oy += (sh - nh) // 2

                    if track_mouth > 0.05 and any(k in nl for k in ["mouth", "lip"]):
                        sw, sh = surf.get_size()
                        nh = max(1, int(sh * (1.0 + track_mouth)))
                        draw_surf = pygame.transform.scale(surf, (sw, nh))
                        oy -= (nh - sh) // 2

                    screen.blit(draw_surf, (pet_x + ox, pet_y + oy))

            pygame.display.flip()

        # Cleanup
        self._cleanup_tracking()
        pygame.quit()
        log.info("Pet runner stopped")
        return True

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def _init_tracking(self) -> None:
        """Initialize face tracker and audio capture."""
        try:
            from drivers.face_tracker.mediapipe_tracker import FaceTracker
            from drivers.face_tracker.blendshape_mapper import BlendShapeMapper
            from drivers.audio.capture import AudioCapture

            self._face_tracker = FaceTracker()
            self._face_tracker.start()
            if self._face_tracker.is_running():
                self._blendshape_mapper = BlendShapeMapper(smoothing_factor=0.5)
                log.success("Face tracking active")
            else:
                log.warning("Face tracking unavailable (no camera or mediapipe)")

            self._audio_capture = AudioCapture()
            self._audio_capture.start()
            if self._audio_capture.is_running():
                log.success("Voice lip-sync active")
        except Exception as e:
            log.warning(f"Tracking initialization failed: {e}")
            self._face_tracker = None
            self._audio_capture = None

    def _cleanup_tracking(self) -> None:
        """Stop tracking components."""
        if self._face_tracker:
            try:
                self._face_tracker.stop()
            except Exception:
                pass
            self._face_tracker = None
        if self._audio_capture:
            try:
                self._audio_capture.stop()
            except Exception:
                pass
            self._audio_capture = None

    # ------------------------------------------------------------------
    # Loading helpers
    # ------------------------------------------------------------------

    def _load_layers(self, pg) -> List[Tuple[str, Any]]:
        """Load layers from self.layers_dir."""
        return self._load_layers_from(self.layers_dir, pg)

    def _load_layers_from(self, directory: Path, pg) -> List[Tuple[str, Any]]:
        """Load PNG layers from a directory."""
        layers: List[Tuple[str, Any]] = []
        if not directory.is_dir():
            return layers
        for f in sorted(directory.glob("*.png")):
            if f.name in ("preview.png", "composite_preview.png"):
                continue
            try:
                surf = pg.image.load(str(f)).convert_alpha()
                layers.append((f.stem, surf))
            except Exception as e:
                log.debug(f"Could not load {f}: {e}")
        return layers

    def _load_frames(self, frames_dir: Path, pg) -> List[Any]:
        """Load pre-rendered frames for fallback mode."""
        frames: List[Any] = []
        for f in sorted(frames_dir.glob("frame_*.png")):
            try:
                frames.append(pg.image.load(str(f)).convert_alpha())
            except Exception:
                pass
        if frames:
            log.info(f"Loaded {len(frames)} pre-rendered frames")
        return frames

    @staticmethod
    def _default_layer(pg) -> Tuple[str, Any]:
        """Create a simple default pet surface when no layers are found."""
        surf = pg.Surface((128, 128), pg.SRCALPHA)
        pg.draw.circle(surf, (255, 200, 200), (64, 64), 50)
        pg.draw.circle(surf, (100, 100, 200), (48, 54), 8)
        pg.draw.circle(surf, (100, 100, 200), (80, 54), 8)
        pg.draw.circle(surf, (255, 150, 150), (64, 75), 12, 2)
        return ("default", surf)


def main() -> int:
    """CLI entry point: run a pet from a layers directory or pet package."""
    import argparse

    parser = argparse.ArgumentParser(description="Run a Live2D desktop pet")
    parser.add_argument("layers_dir", nargs="?", help="Directory of layer PNGs / pet package")
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--tracking", action="store_true", help="Enable face tracking + lip-sync")
    args = parser.parse_args()

    if not args.layers_dir:
        parser.error("layers_dir is required")
    if not Path(args.layers_dir).is_dir():
        log.error(f"Directory not found: {args.layers_dir}")
        return 1

    runner = PetRunner(
        layers_dir=args.layers_dir,
        width=args.width,
        height=args.height,
        fps=args.fps,
        enable_tracking=args.tracking,
    )
    return 0 if runner.run() else 1


if __name__ == "__main__":
    sys.exit(main())
