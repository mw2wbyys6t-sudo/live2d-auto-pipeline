# -*- coding: utf-8 -*-
"""skyline 打包的复杂度守卫。

背景：``_add_skyline_rect`` 曾在重叠时重复插入同一个 x，起点序列失去单调性，
段数按 2 的幂增长 —— 实测 20 个真实部件到 8194 段、``max`` 调用 1053 万次、
单次打包 4 秒。这里用「段数上界」而不是计时来守卫，避免 CI 抖动：
skyline 的段数只应随投放矩形数线性增长（每次最多新增 2 个拐点）。
"""
from __future__ import annotations

from live2d_builder.exporter.texture_atlas import TextureAtlas
from live2d_builder.mesh.uv_unwrapper import UVUnwrapper


def _invariants_hold(skyline: list[list[int]]) -> bool:
    xs = [p[0] for p in skyline]
    if len(xs) != len(set(xs)):
        return False
    if any(b <= a for a, b in zip(xs, xs[1:])):
        return False
    return all(a[1] != b[1] for a, b in zip(skyline, skyline[1:]))


def test_add_rect_keeps_skyline_invariants():
    skyline = [[0, 0]]
    UVUnwrapper._add_skyline_rect(skyline, 10, 0, 20, 5)
    assert _invariants_hold(skyline)

    # 部分重叠：左边界落在段中间，右边界落在另一段中间
    UVUnwrapper._add_skyline_rect(skyline, 5, 5, 40, 3)
    assert _invariants_hold(skyline)

    # 完全包含：一次覆盖多个已存在拐点
    UVUnwrapper._add_skyline_rect(skyline, 0, 0, 200, 1)
    assert _invariants_hold(skyline)


def test_repeated_overlap_does_not_explode_segments():
    """反复部分重叠曾把段数放大到 2 的幂；现在必须保持线性。"""
    skyline = [[0, 0]]
    for i in range(200):
        UVUnwrapper._add_skyline_rect(skyline, i, 0, 3, 1 + (i % 3))
        assert _invariants_hold(skyline)
    # 200 次投放，每段最多 2 个拐点，远小于旧实现的数千段
    assert len(skyline) <= 2 * 200 + 2


def test_pack_meshes_segments_stay_bounded():
    """真实尺度的 35 个部件：段数不得随投放次数指数增长。"""
    boxes = {}
    meshes = {}
    sizes = [
        (625, 828), (613, 381), (556, 254), (556, 246), (132, 737), (124, 718),
        (222, 120), (126, 203), (210, 190), (160, 150), (150, 140), (140, 130),
        (240, 120), (200, 110), (190, 105), (180, 100), (170, 95), (160, 90),
        (150, 85), (120, 60), (118, 58), (110, 55), (100, 50), (100, 48),
        (90, 45), (80, 40), (78, 38), (70, 35), (68, 34), (60, 30),
        (50, 25), (40, 20), (38, 19), (36, 18), (34, 17),
    ]
    peaks = []
    original = UVUnwrapper._add_skyline_rect

    def watched(skyline, x, y, w, h):
        original(skyline, x, y, w, h)
        peaks.append(len(skyline))

    UVUnwrapper._add_skyline_rect = staticmethod(watched)
    try:
        for i, (w, h) in enumerate(sizes):
            name = f"part_{i}"
            boxes[name] = (100, 50, w, h)
            meshes[name] = {"width": 1024, "height": 1536}
        result = UVUnwrapper(atlas_size=4096, padding=2, algorithm="skyline").pack_meshes(
            meshes, content_boxes=boxes)
    finally:
        UVUnwrapper._add_skyline_rect = staticmethod(original)

    assert len(result["uvs"]) == len(sizes)
    # 旧实现此处到 8194 段；正确实现应远小于 2n
    assert max(peaks) <= 2 * len(sizes) + 2, f"段数发散: {max(peaks)}"


def test_texture_atlas_uses_the_same_skyline_implementation():
    """两处曾各有一份平行实现，修一边漏一边就会一边正常一边退化。"""
    assert TextureAtlas._add_skyline_rect.__doc__ is not None
    skyline = [[0, 0]]
    TextureAtlas._add_skyline_rect(skyline, 10, 0, 20, 5)
    assert _invariants_hold(skyline)
