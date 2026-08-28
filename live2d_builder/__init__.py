#!/usr/bin/env python3
"""Live2D builder pipeline: mesh generation, rigging, and Cubism 4 export.

This package provides a complete pipeline for turning layered character
artwork into Live2D Cubism 4 model packages that can be loaded into
Cubism Editor, VTube Studio, and VSeeFace.
"""

from live2d_builder.pipeline import Live2DBuilder, RiggingPipeline
from live2d_builder.mesh.generator import MeshGenerator
from live2d_builder.mesh.uv_unwrapper import UVUnwrapper
from live2d_builder.bones.deformers import BoneHierarchy, DeformerHierarchy
from live2d_builder.blendshapes.parameters import ParameterSet
from live2d_builder.blendshapes.expressions import ExpressionBuilder
from live2d_builder.physics.config import PhysicsBuilder
from live2d_builder.exporter.model3_exporter import Model3Exporter
from live2d_builder.exporter.texture_atlas import TextureAtlas
from live2d_builder.validator.model_validator import ModelValidator

__all__ = [
    "Live2DBuilder",
    "RiggingPipeline",
    "MeshGenerator",
    "UVUnwrapper",
    "BoneHierarchy",
    "DeformerHierarchy",
    "ParameterSet",
    "ExpressionBuilder",
    "PhysicsBuilder",
    "Model3Exporter",
    "TextureAtlas",
    "ModelValidator",
]
