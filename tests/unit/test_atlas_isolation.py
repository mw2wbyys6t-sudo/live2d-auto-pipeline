import numpy as np
from PIL import Image

from live2d_builder.exporter.texture_atlas import TextureAtlas


def test_atlas_padding_uses_each_layers_edge_colors():
    red = Image.new("RGBA", (5, 5), (255, 0, 0, 255))
    blue = Image.new("RGBA", (5, 5), (0, 0, 255, 255))

    result = TextureAtlas(max_size=64, padding=2, power_of_two=False).pack(
        {"red": red, "blue": blue}
    )

    atlas = np.asarray(result["atlases"][0])
    for name, placement in result["uvs"].items():
        x, y = map(int, placement["top_left"])
        expected = (255, 0, 0) if name == "red" else (0, 0, 255)
        assert tuple(atlas[y + 2, x + 2, :3]) == expected
        assert tuple(atlas[y + 2, x + 6, :3]) == expected
