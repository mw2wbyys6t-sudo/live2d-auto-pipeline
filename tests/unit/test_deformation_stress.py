import numpy as np
from PIL import Image

from live2d_builder.mesh.generator import MeshGenerator


def test_generated_mesh_keeps_alpha_support_under_small_affine_stress():
    rgba = np.zeros((32, 32, 4), dtype=np.uint8)
    rgba[8:24, 8:24, :3] = (80, 120, 160)
    rgba[8:24, 8:24, 3] = 255

    mesh = MeshGenerator().generate(Image.fromarray(rgba))

    vertices = mesh["vertices"]
    moved = (vertices - np.array([15.5, 15.5])) * 1.05 + np.array([15.5, 15.5])
    moved += np.array([3, -3])
    assert np.all(np.isfinite(moved))
    assert moved.shape == vertices.shape
