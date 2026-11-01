"""图层边缘外扩与网格边界归属：相邻部件之间不许漏出背景。

历史缺陷：每个图层的网格轮廓落在最外侧像素的**中心**，GPU 按像素中心决定三角形
归属，于是最外那一行/列永远落在三角形之外 —— 每个部件沿轮廓少画约 1px。相邻
部件相接处两边各少一点，露出来的背景在画面上就是一条细亮缝。实测 singer 样例
（1024×1536）里衣服上摆/腰带（477 行）、腰带/裙摆（506 行）、裙摆/裙边（833 行）
三行整行丢失；静止姿态就看得见，动画里还随部件位移时隐时现。
"""

import numpy as np
from PIL import Image, ImageDraw

from live2d_builder.mesh.edge_bleed import EDGE_BLEED_PX, bleed_layers, bleed_rgba
from live2d_builder.mesh.generator import MeshGenerator


def _solid(h=40, w=40, box=(12, 10, 23, 21), color=(30, 120, 210, 255)):
    """PIL 的 rectangle 两端都是闭区间，box 就是像素范围。"""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle(box, fill=color)
    return img


def _center_coverage(mesh, shape):
    """按 GPU 口径（像素中心是否落在三角形内）光栅化网格。

    不能用 ``pixel_grid.rasterize_mesh``：那是 cv2.fillPoly，按面积算覆盖，
    比 GPU 宽松，正好把要测的这个边界差异吃掉。
    """
    h, w = shape
    out = np.zeros((h, w), dtype=bool)
    verts = np.asarray(mesh.get("vertices"), dtype=float)
    if verts.size == 0 or not mesh.get("indices"):
        return out
    for tri in mesh["indices"]:
        p = verts[list(tri)]
        x0 = max(int(np.floor(p[:, 0].min())), 0)
        x1 = min(int(np.ceil(p[:, 0].max())) + 1, w)
        y0 = max(int(np.floor(p[:, 1].min())), 0)
        y1 = min(int(np.ceil(p[:, 1].max())) + 1, h)
        if x1 <= x0 or y1 <= y0:
            continue
        x, y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        e = [(p[(i + 1) % 3][0] - p[i][0]) * (y - p[i][1])
             - (p[(i + 1) % 3][1] - p[i][1]) * (x - p[i][0]) for i in range(3)]
        out[y0:y1, x0:x1] |= ((e[0] >= 0) & (e[1] >= 0) & (e[2] >= 0)) | \
                             ((e[0] <= 0) & (e[1] <= 0) & (e[2] <= 0))
    return out


class TestBleedRgba:
    def test_color_and_alpha_reach_one_pixel_outside_the_shape(self):
        img = _solid()
        out = np.asarray(bleed_rgba(img, 1))

        # 原形状是 x 12..23、y 10..21；外扩一圈后 11..24 / 9..22 带上同样的颜色
        assert tuple(out[9, 16]) == (30, 120, 210, 255), "上边框外扩缺失"
        assert tuple(out[22, 16]) == (30, 120, 210, 255), "下边框外扩缺失"
        assert tuple(out[15, 11]) == (30, 120, 210, 255), "左边框外扩缺失"
        assert tuple(out[15, 24]) == (30, 120, 210, 255), "右边框外扩缺失"
        # 再往外保持透明：外扩半径是 1，不是无限蔓延
        assert out[8, 16, 3] == 0
        assert out[23, 15, 3] == 0

    def test_transparent_pixels_do_not_wrap_around_the_image(self):
        img = _solid(box=(0, 0, 9, 9))
        out = np.asarray(bleed_rgba(img, 1))

        assert out[0, 0, 3] == 255
        assert out[0, -1, 3] == 0, "左边缘不该回卷到右边缘"
        assert out[-1, 0, 3] == 0, "上边缘不该回卷到下边缘"

    def test_soft_edge_keeps_its_gradient_instead_of_a_hard_rim(self):
        """腮红/眼泪这类渐变层：外扩取邻域最大 alpha，不能直接填 255。"""
        arr = np.zeros((60, 60, 4), dtype=np.uint8)
        arr[..., :3] = (250, 170, 170)
        yy, xx = np.mgrid[0:60, 0:60]
        arr[..., 3] = np.clip(40.0 - np.hypot(xx - 30.0, yy - 30.0) * 4.0, 0, 40)
        img = Image.fromarray(arr, "RGBA")

        out = np.asarray(bleed_rgba(img, 1))
        ring = (np.asarray(img)[..., 3] == 0) & (out[..., 3] > 0)

        assert ring.any(), "渐变层也要外扩"
        assert out[..., 3][ring].max() <= 40, "渐变外缘不能出现 255 硬边"

    def test_bleed_layers_keeps_the_drawing_order(self):
        layers = {"a": _solid(), "b": _solid(box=(0, 0, 5, 5))}
        out = bleed_layers(layers, 1)
        assert list(out) == ["a", "b"]

    def test_zero_radius_is_a_no_op(self):
        img = _solid()
        assert np.array_equal(np.asarray(bleed_rgba(img, 0)), np.asarray(img))
        assert EDGE_BLEED_PX >= 1


class TestPartsMeetWithoutAGap:
    """上下相接的两个部件，接缝那一行必须被两边的网格盖住。"""

    @staticmethod
    def _pair(box_top, box_bottom, h=200, w=200):
        top = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bot = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(top).rectangle(box_top, fill=(40, 60, 80, 255))
        ImageDraw.Draw(bot).rectangle(box_bottom, fill=(90, 70, 40, 255))
        return top, bot

    def _seam_lost(self, top, bot, bleed):
        """接缝带内（避开外轮廓两端的角）丢了多少像素。"""
        ink = (np.asarray(top)[..., 3] > 0) | (np.asarray(bot)[..., 3] > 0)
        cov = np.zeros_like(ink)
        for img in (top, bot):
            src = bleed_rgba(img, bleed) if bleed else img
            cov |= _center_coverage(
                MeshGenerator().generate_combined_mesh(src), ink.shape)
        band = ink[95:105, 45:155]
        return int((band & ~cov[95:105, 45:155]).sum())

    def test_bleed_closes_the_horizontal_seam(self):
        # 上摆到 99 结束、裙摆从 100 开始 —— 与实测躯干的 477/506 行同一情形
        top, bot = self._pair((40, 20, 159, 99), (40, 100, 159, 179))

        assert self._seam_lost(top, bot, 0) > 100, "不外扩时接缝整行丢失"
        assert self._seam_lost(top, bot, EDGE_BLEED_PX) == 0

    def test_contour_epsilon_keeps_the_outline_from_cutting_inward(self):
        """epsilon 取 contour_spacing（12px）时，圆弧轮廓被弦切成内接多边形。

        弦切掉的边圈像素落在网格外，等价于部件沿轮廓缩了 1px 多。
        """
        h = w = 200
        yy, xx = np.mgrid[0:h, 0:w]
        disc = (xx - 100) ** 2 + (yy - 100) ** 2 <= 60 ** 2
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        img.paste((40, 60, 80, 255), mask=Image.fromarray(
            (disc * 255).astype(np.uint8), "L"))

        gen = MeshGenerator()
        gen.CONTOUR_EPSILON_PX = 12.0
        coarse = _center_coverage(gen.generate_combined_mesh(img), disc.shape)
        gen.CONTOUR_EPSILON_PX = 1.0
        fine = _center_coverage(gen.generate_combined_mesh(img), disc.shape)

        coarse_lost = int((disc & ~coarse).sum())
        fine_lost = int((disc & ~fine).sum())
        assert coarse_lost > 100, coarse_lost
        assert fine_lost < coarse_lost / 4, (fine_lost, coarse_lost)
