#!/usr/bin/env python3
"""
Live2D Master Agent - Drivers Module

Hardware and rendering drivers:
- face_tracker: MediaPipe-based real-time face tracking with ARKit blendshapes
- audio: Microphone capture and audio feature analysis
- desktop_pet: Transparent desktop pet window and animation system
- live2d_runtime: Software Live2D-like renderer (PNG layer compositing)
"""

from drivers.face_tracker import FaceTracker, BlendShapeMapper
from drivers.audio import AudioCapture
from drivers.desktop_pet import DesktopPetWindow, DesktopPetAnimator, DesktopPet
from drivers.live2d_runtime import Live2DRenderer

__all__ = [
    "FaceTracker",
    "BlendShapeMapper",
    "AudioCapture",
    "DesktopPetWindow",
    "DesktopPetAnimator",
    "DesktopPet",
    "Live2DRenderer",
]
