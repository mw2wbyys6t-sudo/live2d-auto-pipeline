#!/usr/bin/env python3
"""图层边缘外扩（edge bleed），让相邻部件在渲染时互相压住半个像素。

分层的每一层都是硬 alpha 切出来的：部件内 alpha=255，部件外 alpha=0。网格轮廓
落在最外侧像素的**中心**，所以每个部件渲染时都会沿轮廓少画约一个像素；两个
部件相接处两边各少一点，露出来的背景就是画面上的一条细亮缝。实测 singer 样例
中躯干 477/506/833 三行（衣服上摆/腰带、腰带/裙摆、裙摆/裙边）整行丢失，静止
姿态下就看得见，动起来还会随部件位移时隐时现。

这里把每层的颜色和 alpha 向外扩 ``radius`` 像素：

* 颜色取最近的实像素，扩出来的环与边界同色，不会出现黑边或白边；
* alpha 取四邻域里的**最大值**而不是直接填 255 —— 腮红、眼泪这类柔和渐变层的
  外缘 alpha 本来就接近 0，填 255 会在柔和轮廓外拉出一圈硬边。

扩出来的环对网格来说就是"重叠量"：轮廓采样因此外移 ``radius`` 像素，相邻部件
互相压住，接缝闭合。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
from PIL import Image

#: 外扩像素数。1px 已能闭合最常见的半像素接缝，再多只会把剪影整体撑大。
EDGE_BLEED_PX = 1


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """把数组整体平移，空出来的边填 0（不用 np.roll，避免跨边回卷）。"""
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    out[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = \
        a[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
    return out


def bleed_rgba(img: Image.Image, radius: int = EDGE_BLEED_PX) -> Image.Image:
    """把不透明区域的颜色与 alpha 向透明邻域外扩 ``radius`` 圈。"""
    if radius <= 0:
        return img
    arr = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3].copy()
    rgb = arr[:, :, :3].copy()

    for _ in range(radius):
        known = alpha > 0
        if not known.any():
            break
        best_a = np.zeros_like(alpha)
        best_rgb = np.zeros_like(rgb)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sa = _shift(alpha, dy, dx)
            srgb = _shift(rgb, dy, dx)
            take = _shift(known, dy, dx) & (sa > best_a)
            best_a[take] = sa[take]
            best_rgb[take] = srgb[take]
        fill = (alpha == 0) & (best_a > 0)
        if not fill.any():
            break
        alpha[fill] = best_a[fill]
        rgb[fill] = best_rgb[fill]

    out = arr.copy()
    out[:, :, :3] = rgb
    out[:, :, 3] = alpha
    return Image.fromarray(out, "RGBA")


def bleed_layers(layers: Dict[str, Image.Image],
                 radius: int = EDGE_BLEED_PX) -> Dict[str, Image.Image]:
    """逐层外扩；保持原有键序（层序就是绘制顺序）。"""
    return {name: bleed_rgba(img, radius) for name, img in layers.items()}
