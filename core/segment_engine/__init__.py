#!/usr/bin/env python3
"""
Live2D Master Agent - core.segment_engine

Re-exports the public segmentation / composition classes:

- :class:`SemanticSegmenter` (aliased as ``SemanticLayerer`` for back-compat)
- :class:`AmodalCompleter`
- :class:`LayerComposer`
- :class:`KMeansLayerer` (re-exported from :mod:`core.segment_engine.kmeans`)
- :class:`PartIdentifier`
- :class:`Layer52Generator`
"""

from core.segment_engine.amodal import AmodalCompleter
from core.segment_engine.composer import LayerComposer
from core.segment_engine.kmeans import KMeansLayerer
from core.segment_engine.layers52 import (
    Layer52Generator,
    STANDARD_PARAMS,
    STANDARD_PHYSICS,
    LIVE2D_52_LAYERS,
)
from core.segment_engine.part_identifier import PartIdentifier
from core.segment_engine.semantic import SemanticSegmenter, SemanticLayerer

__all__ = [
    "AmodalCompleter",
    "KMeansLayerer",
    "LayerComposer",
    "Layer52Generator",
    "PartIdentifier",
    "SemanticSegmenter",
    "SemanticLayerer",
    "STANDARD_PARAMS",
    "STANDARD_PHYSICS",
    "LIVE2D_52_LAYERS",
]
