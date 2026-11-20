"""大画布导出：图集尺寸必须随最大部件自适应。

4K 立绘（2731×4096）的单块部件会超过默认 2048 图集，若不抬升，
导出会在打包阶段抛 "exceeds max atlas size" 直接失败。
"""

from __future__ import annotations

from PIL import Image

from live2d_builder.pipeline import Live2DBuilder


def _builder(tmp_path, atlas_size: int = 2048) -> Live2DBuilder:
    return Live2DBuilder(output_dir=str(tmp_path), character_name="t", atlas_size=atlas_size)


class TestAtlasCapacity:
    def test_small_canvas_keeps_default(self, tmp_path):
        builder = _builder(tmp_path)
        builder._ensure_atlas_capacity({"face": Image.new("RGBA", (1024, 1536))})
        assert builder.atlas_size == 2048

    def test_four_k_layer_raises_atlas(self, tmp_path):
        builder = _builder(tmp_path)
        builder._ensure_atlas_capacity({"hair_back": Image.new("RGBA", (2731, 4096))})
        # 4096 高的部件：去掉 padding 刚好装进 4096 图集，不跳到 8192（省内存）
        assert builder.atlas_size == 4096
        assert builder.uv_unwrapper.atlas_size == 4096
        assert builder.uv_unwrapper.padding == 0
        assert builder.exporter.max_atlas_size == 4096

    def test_oversized_layer_goes_to_8192(self, tmp_path):
        builder = _builder(tmp_path)
        builder._ensure_atlas_capacity({"hair_back": Image.new("RGBA", (5000, 6000))})
        assert builder.atlas_size == 8192

    def test_never_downgrades(self, tmp_path):
        builder = _builder(tmp_path, atlas_size=4096)
        builder._ensure_atlas_capacity({"face": Image.new("RGBA", (512, 768))})
        assert builder.atlas_size == 4096

    def test_empty_layers_is_noop(self, tmp_path):
        builder = _builder(tmp_path)
        builder._ensure_atlas_capacity({})
        assert builder.atlas_size == 2048
