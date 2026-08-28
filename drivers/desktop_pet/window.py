#!/usr/bin/env python3
"""
Cross-platform transparent desktop pet window using pygame.

Creates a frameless, always-on-top, per-pixel-alpha transparent window
that can render pygame surfaces (Live2D frames, static images, etc.)
and respond to user interaction (click, drag, right-click, key press).
"""

import os
import sys
import time
from typing import Optional, Dict, Tuple, Any

from core.logger import get_logger

log = get_logger("pet_window")

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False
    pygame = None  # type: ignore

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    Image = None  # type: ignore


class DesktopPetWindow:
    """Frameless transparent desktop pet window.

    Parameters
    ----------
    width, height : int
        Window dimensions in pixels.
    x, y : int
        Initial window position on screen.
    always_on_top : bool
        Attempt to keep the window above other windows.
    frameless : bool
        Remove window decorations (title bar, borders).
    """

    def __init__(
        self,
        width: int = 300,
        height: int = 400,
        x: int = 100,
        y: int = 100,
        always_on_top: bool = True,
        frameless: bool = True,
    ):
        self.width = width
        self.height = height
        self._x = x
        self._y = y
        self._target_x = x
        self._target_y = y
        self._always_on_top = always_on_top
        self._frameless = frameless

        self._screen = None
        self._clock = None
        self._running = False
        self._visible = False
        self._fps = 60
        self._events_queue: list = []
        self._dragging = False
        self._drag_offset: Tuple[int, int] = (0, 0)
        self._move_start_time: float = 0.0
        self._move_start: Tuple[int, int] = (x, y)
        self._move_duration: float = 0.3

    # ------------------------------------------------------------------
    # Window control
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Create and display the window."""
        if not _PYGAME_AVAILABLE:
            log.error("pygame is required. Install: pip install pygame")
            return

        if self._running:
            return

        try:
            pygame.init()
            # Position window before creation
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{self._x},{self._y}"

            flags = 0
            if self._frameless:
                flags |= pygame.NOFRAME
            flags |= pygame.SRCALPHA

            self._screen = pygame.display.set_mode((self.width, self.height), flags)
            pygame.display.set_caption("Live2D Desktop Pet")

            self._clock = pygame.time.Clock()
            self._running = True
            self._visible = True

            # Attempt always-on-top via platform-specific hints
            self._set_always_on_top()

            log.success(f"Desktop pet window shown ({self.width}x{self.height} at {self._x},{self._y})")
        except Exception as e:
            log.error(f"Failed to show pet window: {e}")
            self._running = False

    def hide(self) -> None:
        """Hide the window (iconify) without destroying it."""
        if not _PYGAME_AVAILABLE or self._screen is None:
            return
        try:
            pygame.display.iconify()
            self._visible = False
        except Exception as e:
            log.warning(f"Failed to hide window: {e}")

    def _set_always_on_top(self) -> None:
        """Attempt to set the window always-on-top (best-effort per platform)."""
        if not self._always_on_top or not _PYGAME_AVAILABLE:
            return
        try:
            # On Windows, set window topmost via ctypes
            if os.name == "nt":
                import ctypes
                hwnd = pygame.display.get_wm_info().get("window")
                if hwnd:
                    HWND_TOPMOST = -1
                    SWP_NOMOVE = 0x0002
                    SWP_NOSIZE = 0x0001
                    ctypes.windll.user32.SetWindowPos(
                        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE
                    )
            # On Linux X11, set _NET_WM_STATE_ABOVE
            elif sys.platform.startswith("linux"):
                try:
                    wm_info = pygame.display.get_wm_info()
                    if "window" in wm_info:
                        # Best-effort; full EWMH would require Xlib
                        pass
                except Exception:
                    pass
        except Exception as e:
            log.debug(f"Could not set always-on-top: {e}")

    # ------------------------------------------------------------------
    # Position / appearance
    # ------------------------------------------------------------------

    def set_position(self, x: int, y: int) -> None:
        """Immediately set the window position."""
        self._x = x
        self._y = y
        self._target_x = x
        self._target_y = y
        if _PYGAME_AVAILABLE and self._screen is not None:
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"
            # pygame does not have a native set_pos; use SDL env var trick
            # We create a new window position by manipulating the environment
            # This is a known limitation — position changes take effect on
            # the next display flip with some drivers.

    def set_opacity(self, opacity: float) -> None:
        """Set window opacity (0.0 fully transparent to 1.0 opaque)."""
        opacity = max(0.0, min(1.0, opacity))
        if _PYGAME_AVAILABLE and self._screen is not None:
            try:
                # Per-pixel alpha is already used; global opacity via set_alpha
                # on the window is not directly supported in pygame.
                # We simulate by adjusting the background clear alpha.
                self._opacity = opacity
            except Exception as e:
                log.debug(f"set_opacity not fully supported: {e}")

    def move_to(self, x: int, y: int, smooth: bool = True) -> None:
        """Move the window to a target position, optionally smoothly.

        Smooth movement interpolates over ~0.3 seconds via ``update()`` ticks.
        """
        self._target_x = x
        self._target_y = y
        if not smooth:
            self.set_position(x, y)
        else:
            self._move_start = (self._x, self._y)
            self._move_start_time = time.time()

    def get_screen_size(self) -> Tuple[int, int]:
        """Return the primary display resolution as ``(width, height)``."""
        if _PYGAME_AVAILABLE:
            try:
                info = pygame.display.Info()
                return (info.current_w, info.current_h)
            except Exception:
                pass
        # Fallback: try tkinter
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            w = root.winfo_screenwidth()
            h = root.winfo_screenheight()
            root.destroy()
            return (w, h)
        except Exception:
            return (1920, 1080)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_frame(self, surface: Any) -> None:
        """Render a pygame Surface or PIL Image to the window.

        Parameters
        ----------
        surface : pygame.Surface or PIL.Image.Image
            The frame to blit. Will be scaled to window size.
        """
        if not _PYGAME_AVAILABLE or self._screen is None:
            return

        # Convert PIL Image to pygame Surface if needed
        if _PIL_AVAILABLE and isinstance(surface, Image.Image):
            mode = surface.mode
            if mode != "RGBA":
                surface = surface.convert("RGBA")
            raw = surface.tobytes("raw", "RGBA")
            surf = pygame.image.fromstring(raw, surface.size, "RGBA")
        elif _PYGAME_AVAILABLE and isinstance(surface, pygame.Surface):
            surf = surface
        else:
            log.warning(f"render_frame received unsupported type: {type(surface)}")
            return

        # Scale to fit window
        if surf.get_size() != (self.width, self.height):
            surf = pygame.transform.smoothscale(surf, (self.width, self.height))

        self._screen.fill((0, 0, 0, 0))
        self._screen.blit(surf, (0, 0))

    def flip(self) -> None:
        """Update the display (call after render_frame)."""
        if _PYGAME_AVAILABLE and self._screen is not None:
            pygame.display.flip()
            if self._clock:
                self._clock.tick(self._fps)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def handle_events(self) -> Dict[str, Any]:
        """Poll and process pygame events.

        Returns a dict describing the latest user interaction:
        ``{"type": "click"|"drag"|"right_click"|"key"|"quit"|None, ...}``
        """
        result: Dict[str, Any] = {"type": None}
        if not _PYGAME_AVAILABLE:
            return result

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._running = False
                result = {"type": "quit"}
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self._running = False
                    result = {"type": "quit"}
                else:
                    result = {"type": "key", "key": event.key}
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    mx, my = event.pos
                    self._dragging = True
                    self._drag_offset = (mx, my)
                    result = {"type": "click", "x": mx, "y": my, "button": 1}
                elif event.button == 3:
                    result = {"type": "right_click", "x": event.pos[0], "y": event.pos[1]}
            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1:
                    self._dragging = False
                    result = {"type": "release", "x": event.pos[0], "y": event.pos[1]}
            elif event.type == pygame.MOUSEMOTION:
                if self._dragging:
                    mx, my = event.pos
                    screen_w, screen_h = self.get_screen_size()
                    new_x = self._x + mx - self._drag_offset[0]
                    new_y = self._y + my - self._drag_offset[1]
                    new_x = max(0, min(new_x, screen_w - self.width))
                    new_y = max(0, min(new_y, screen_h - self.height))
                    self.set_position(new_x, new_y)
                    result = {"type": "drag", "x": new_x, "y": new_y}

        return result

    # ------------------------------------------------------------------
    # Per-frame update (for smooth movement, etc.)
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Call once per frame to advance smooth animations/movement."""
        # Smooth movement interpolation
        if (self._x, self._y) != (self._target_x, self._target_y):
            elapsed = time.time() - self._move_start_time
            t = min(1.0, elapsed / self._move_duration)
            # Ease out cubic
            t = 1.0 - (1.0 - t) ** 3
            sx, sy = self._move_start
            self._x = int(sx + (self._target_x - sx) * t)
            self._y = int(sy + (self._target_y - sy) * t)
            if _PYGAME_AVAILABLE and self._screen is not None:
                os.environ["SDL_VIDEO_WINDOW_POS"] = f"{self._x},{self._y}"

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Return True if the window is open and running."""
        return self._running

    def close(self) -> None:
        """Destroy the window and clean up pygame."""
        self._running = False
        self._visible = False
        if _PYGAME_AVAILABLE:
            try:
                pygame.display.quit()
                pygame.quit()
            except Exception:
                pass
        self._screen = None
        log.info("Desktop pet window closed")

    def __del__(self) -> None:
        if self._running:
            self.close()
