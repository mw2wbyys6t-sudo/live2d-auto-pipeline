import numpy as np
from PIL import Image

from live2d_builder.mesh.generator import MeshGenerator


def test_generated_mesh_covers_every_visible_alpha_pixel():
    rgba = np.zeros((24, 24, 4), dtype=np.uint8)
    rgba[5:19, 6:18, :3] = (100, 120, 140)
    rgba[5:19, 6:18, 3] = 255
    rgba[4, 10, :3] = (100, 120, 140)
    rgba[4, 10, 3] = 16

    mesh = MeshGenerator().generate(Image.fromarray(rgba))

    assert mesh["vertices"].shape[0] > 0
    vertices = mesh["vertices"]
    triangles = np.asarray(mesh["indices"], dtype=int)
    assert triangles.size
    assert np.all(vertices[:, 0] >= 0)
    assert np.all(vertices[:, 0] < rgba.shape[1])
    assert np.all(vertices[:, 1] >= 0)
    assert np.all(vertices[:, 1] < rgba.shape[0])
