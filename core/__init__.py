#!/usr/bin/env python3
"""
Live2D Master Agent v10.0 - Core Package

Production-grade AI character creation pipeline:
- Image generation (Pollinations, Seedream, SenseNova)
- Semantic segmentation (SAM+ISNet+Amodal)
- Live2D Cubism4 auto-rigging
- Character consistency system
- Desktop pet with real-time tracking
- LLM chat + voice + emotion linkage
"""

from core.version import __version__, FULL_VERSION_STRING

__all__ = ["__version__", "FULL_VERSION_STRING"]
