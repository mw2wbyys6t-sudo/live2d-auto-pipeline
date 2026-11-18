"""被刘海遮挡的虹膜补全：几何按对侧、颜色取饱和簇。

现场（2026-11-14）：右眼被刘海压住，irides_right 只有 50×15（高宽比 0.30），
左眼是 64×61；眨眼时右侧会露出空洞。补全后应变成与左眼同尺度的椭圆，
且颜色是虹膜的蓝色而不是睫毛的灰褐色。
"""

import numpy as np
from PIL import Image, ImageDraw

from core.segment_engine.composer import LayerComposer


def _layer(path, array, bbox):
    pixels = array[..., 3] > 0
    ys, xs = np.where(pixels)
    return {
        "name": path.stem,
        "path": str(path),
        "bbox": bbox,
        "size": (int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)),
        "pixel_count": int(pixels.sum()),
        "mean_color": tuple(int(c) for c in array[pixels][:, :3].mean(axis=0)),
    }


def _canvas(w=220, h=140):
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def test_occluded_iris_is_completed_to_partner_scale(tmp_path):
    left_img = _canvas()
    ImageDraw.Draw(left_img).ellipse((40, 30, 100, 86), fill=(60, 90, 200, 255))
    right_img = _canvas()
    draw = ImageDraw.Draw(right_img)
    draw.rectangle((120, 52, 180, 61), fill=(60, 90, 200, 255))   # 被遮住的细条
    draw.rectangle((120, 48, 180, 52), fill=(25, 25, 30, 255))    # 睫毛压在上面

    left_path, right_path = tmp_path / "irides_left.png", tmp_path / "irides_right.png"
    left_img.save(left_path)
    right_img.save(right_path)
    layers = {
        "irides_left": _layer(left_path, np.array(left_img), (40, 30, 100, 86)),
        "irides_right": _layer(right_path, np.array(right_img), (120, 48, 180, 61)),
    }
    # 原图眼睛区域里有蓝色虹膜（补全色从原图饱和像素取）
    source = np.zeros((140, 220, 3), dtype=np.uint8)
    source[30:86, 40:100] = (60, 90, 200)
    source[48:61, 120:180] = (60, 90, 200)

    LayerComposer()._complete_occluded_irises(layers, source, h=140, w=220)

    info = layers["irides_right"]
    assert info["size"][1] >= 40, f"补全后高度仍不足: {info['size']}"
    assert info["pixel_count"] > layers["irides_left"]["pixel_count"] * 0.5
    r, g, b = info["mean_color"]
    assert b > r + 20, f"补全颜色应偏蓝（虹膜本色），实际 {info['mean_color']}"


def test_complete_iris_keeps_visible_pixels(tmp_path):
    left_img = _canvas()
    ImageDraw.Draw(left_img).ellipse((40, 30, 100, 86), fill=(60, 90, 200, 255))
    right_img = _canvas()
    draw = ImageDraw.Draw(right_img)
    draw.rectangle((120, 52, 180, 61), fill=(200, 60, 60, 255))  # 可见部分故意用红色

    left_path, right_path = tmp_path / "irides_left.png", tmp_path / "irides_right.png"
    left_img.save(left_path)
    right_img.save(right_path)
    layers = {
        "irides_left": _layer(left_path, np.array(left_img), (40, 30, 100, 86)),
        "irides_right": _layer(right_path, np.array(right_img), (120, 52, 180, 61)),
    }
    source = np.zeros((140, 220, 3), dtype=np.uint8)
    source[30:86, 40:100] = (60, 90, 200)
    source[52:61, 120:180] = (60, 90, 200)

    LayerComposer()._complete_occluded_irises(layers, source, h=140, w=220)

    out = np.array(Image.open(right_path))
    # 原来可见的那条必须原样保留，不能被补全色覆盖
    assert tuple(out[56, 150][:3]) == (200, 60, 60)


def test_completion_stays_within_partner_iris_size(tmp_path):
    """补全尺寸必须跟「对侧虹膜核心」一致，不能拿整个眼窝的图层 bbox 当参照
    （旧实现补出 64×61 的圆盘，直接盖住眼白和刘海）。"""
    left_img = _canvas()
    ImageDraw.Draw(left_img).ellipse((40, 30, 100, 86), fill=(60, 90, 200, 255))
    right_img = _canvas()
    ImageDraw.Draw(right_img).rectangle((120, 52, 180, 61), fill=(60, 90, 200, 255))

    left_path, right_path = tmp_path / "irides_left.png", tmp_path / "irides_right.png"
    left_img.save(left_path)
    right_img.save(right_path)
    layers = {
        "irides_left": _layer(left_path, np.array(left_img), (40, 30, 100, 86)),
        "irides_right": _layer(right_path, np.array(right_img), (120, 52, 180, 61)),
    }
    source = np.zeros((140, 220, 3), dtype=np.uint8)
    source[30:86, 40:100] = (60, 90, 200)   # 对侧虹膜核心 60×56
    source[52:61, 120:180] = (60, 90, 200)  # 本侧可见的一条 61×9

    LayerComposer()._complete_occluded_irises(layers, source, h=140, w=220)

    width, height = layers["irides_right"]["size"]
    assert width <= 66 and height <= 62, f"补全越界: {width}×{height}"


def test_iris_core_accepts_rgba_source():
    """compose() 传入的原图是 RGBA；旧实现直接 reshape(-1, 3) 会抛 ValueError，
    导致整个 LayerComposer 失败并回退成「直接导出掩码」。"""
    source = np.zeros((60, 80, 4), dtype=np.uint8)
    source[10:40, 20:60] = (60, 90, 200, 255)

    core = LayerComposer()._iris_core(source, (20, 10, 60, 40))

    assert core is not None
    r, g, b = (int(c) for c in np.median(core["pixels"], axis=0))
    assert b > r + 20, f"RGBA 原图取色应偏蓝，实际 {(r, g, b)}"
