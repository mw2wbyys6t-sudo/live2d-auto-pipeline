"""图集容量：整画布图层必须按内容裁剪后再打包。

现场（2026-11-18，4K 立绘 2731×4096）：导出的每层 PNG 都是整张画布，
按画布尺寸打包 → 32 层占 32 页 4096²（约 2GB 贴图）。更糟的是部件本身
正好 4096 高时，加上 padding 的 rect 超过图集上限，一整批都放不进去：
``hair_back: 图集中没有该层的 UV``，moc3 直接不可部署。
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from live2d_builder.exporter.moc3_pipeline import uv_for_vertex
from live2d_builder.exporter.texture_atlas import TextureAtlas


def _canvas(size, box, color=(10, 200, 30, 255)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste(color, box)
    return img


class TestCropping:
    def test_canvas_sized_layers_are_packed_as_their_content(self):
        size = (240, 400)
        layers = {
            "face": _canvas(size, (10, 20, 40, 60), (255, 200, 180, 255)),
            "neck": _canvas(size, (100, 300, 150, 360), (200, 170, 140, 255)),
        }

        result = TextureAtlas(max_size=256, padding=0, power_of_two=False).pack(layers)

        assert len(result["atlases"]) == 1, "整画布打包会一层一页"
        assert result["uvs"]["face"]["bbox"] == (10, 20, 30, 40)
        assert result["uvs"]["neck"]["bbox"] == (100, 300, 50, 60)
        atlas = np.asarray(result["atlases"][0])
        assert atlas.shape[0] <= 256 and atlas.shape[1] <= 256
        x, y = map(int, result["uvs"]["face"]["top_left"])
        assert tuple(atlas[y + 1, x + 1][:3]) == (255, 200, 180)

    def test_padding_gives_way_when_a_part_fills_the_atlas(self):
        """4K 画布高 4096：part + 2px padding 越界时不能让整批失败。"""
        layer = Image.new("RGBA", (254, 254), (9, 9, 9, 255))

        result = TextureAtlas(max_size=256, padding=2, power_of_two=False).pack(
            {"hair_back": layer}
        )

        assert result["uvs"], "padding 把 rect 撑过上限，整批没有 UV"
        assert result["uvs"]["hair_back"]["bbox"] == (0, 0, 254, 254)
        assert np.asarray(result["atlases"][0]).shape[:2] == (254, 254)

    def test_full_height_part_still_gets_a_non_empty_uv(self):
        """复现现场：``hair_back`` 与图集等高（4096 vs 4096）时 UV 不能为空。

        旧实现里 rect = 高 + 2×padding 超过图集上限，skyline 一个都放不下，
        整批 UV 为空，moc3 编译报「图集中没有该层的 UV」。
        """
        layer = Image.new("RGBA", (8, 512), (12, 34, 56, 255))

        result = TextureAtlas(max_size=512, padding=2, power_of_two=False).pack(
            {"hair_back": layer}
        )

        uv = result["uvs"]["hair_back"]
        assert uv["u1"] > uv["u0"] and uv["v1"] > uv["v0"], uv
        assert uv["bbox"] == (0, 0, 8, 512)
        # 顶点映射到 UV 后仍落在 rect 内且不退化
        u0, v0 = uv_for_vertex(uv, 0, 0, 8, 512)
        u1, v1 = uv_for_vertex(uv, 8, 512, 8, 512)
        assert (u0, v0) == pytest.approx((uv["u0"], uv["v0"]))
        assert (u1, v1) == pytest.approx((uv["u1"], uv["v1"]))

    def test_atlas_page_contains_only_the_cropped_content(self):
        layer = _canvas((200, 200), (50, 60, 70, 90), (10, 200, 30, 255))
        atlas = TextureAtlas(max_size=128, padding=0, power_of_two=False)
        packed = atlas.pack({"layer": layer})

        page = np.asarray(packed["atlases"][0])
        x, y = map(int, packed["uvs"]["layer"]["top_left"])
        assert tuple(page[y + 1, x + 1][:3]) == (10, 200, 30)
        # 内容只有 20×30，打出来不该是整张 200×200 的缩放版
        assert page.shape[:2] == (30, 20)

    def test_uv_rect_excludes_the_padding_ring(self):
        """padding 只做间隔与防溢色，UV 必须只覆盖内容。

        与 Cubism 的精灵图集约定一致（``SpriteDataCreate_Util.TEXTURE_MARGINE = 2``：
        2px 只作为精灵之间的间隔，UV 四边形只框内容）。UV 若把 padding 也框进去，
        双线性采样就会把邻层或透明边缘读进来。
        """
        layer = _canvas((64, 64), (10, 12, 30, 40))

        packed = TextureAtlas(max_size=128, padding=2, power_of_two=False).pack(
            {"a": layer})

        uv = packed["uvs"]["a"]
        left, top = uv["top_left"]
        right, bottom = uv["bottom_right"]
        assert (right - left, bottom - top) == (20, 28), uv

    def test_padding_ring_copies_the_edge_pixels(self):
        """2px 间隔里要填内容边缘色：否则缩放采样会在部件四周读进透明。"""
        layer = _canvas((64, 64), (10, 12, 30, 40), (10, 200, 30, 255))

        packed = TextureAtlas(max_size=128, padding=2, power_of_two=False).pack(
            {"a": layer})

        page = np.asarray(packed["atlases"][0])
        left, top = map(int, packed["uvs"]["a"]["top_left"])
        # 上边与左边各 2px 的环都是内容第一行/第一列的复制
        assert tuple(page[top - 1, left + 1][:3]) == (10, 200, 30)
        assert tuple(page[top + 1, left - 1][:3]) == (10, 200, 30)


class TestUvForVertex:
    RECT = {"u0": 0.5, "v0": 0.25, "u1": 0.75, "v1": 0.5,
            "bbox": (100, 200, 50, 40)}

    def test_canvas_coords_are_translated_by_the_crop_box(self):
        assert uv_for_vertex(self.RECT, 100, 200, 1000, 1000) == (0.5, 0.25)
        u, v = uv_for_vertex(self.RECT, 150, 240, 1000, 1000)
        assert (u, v) == pytest.approx((0.75, 0.5))

    def test_vertices_outside_the_crop_are_clamped(self):
        assert uv_for_vertex(self.RECT, 0, 0, 1000, 1000) == (0.5, 0.25)
        assert uv_for_vertex(self.RECT, 200, 300, 1000, 1000) == (0.75, 0.5)

    def test_missing_bbox_falls_back_to_the_whole_canvas(self):
        rect = {"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0}

        u, v = uv_for_vertex(rect, 500, 250, 1000, 1000)

        assert (u, v) == pytest.approx((0.5, 0.25))
