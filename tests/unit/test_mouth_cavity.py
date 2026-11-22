"""口内层不能是嘴唇的复制品。

实测（2026-11-14，1024×1536 偶像立绘）：mouth 与 mouth_cavity 的 bbox 与
像素数完全相同（161×134 / 13,129）——旧实现直接复制嘴唇整块 alpha。结果是
分层列表里两条同尺寸图层，嘴巴张开时整片嘴唇都会变成口腔色。
"""

import numpy as np
from PIL import Image, ImageDraw

from core.segment_engine.composer import LayerComposer


def _write_mouth(tmp_path, draw_fn):
    canvas = Image.new("RGBA", (160, 80), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(canvas))
    path = tmp_path / "mouth.png"
    canvas.save(path)
    return path


def test_cavity_is_strictly_smaller_than_lips(tmp_path):
    path = _write_mouth(tmp_path, lambda d: d.ellipse((20, 20, 140, 60), fill=(60, 40, 45, 255)))
    composer = LayerComposer()

    cavity = composer._synthesize_mouth_cavity({"mouth": {"path": str(path)}}, ["mouth"], 80, 160)

    assert cavity is not None, "有明确嘴唇区域时必须生成口内层"
    lip_alpha = np.array(Image.open(path))[..., 3] > 0
    cavity_alpha = cavity[..., 3] > 0
    assert cavity_alpha.sum() < lip_alpha.sum(), "口内层必须比嘴唇小一圈"

    def bbox(mask):
        ys, xs = np.where(mask)
        return int(ys.min()), int(xs.min()), int(ys.max()), int(xs.max())

    assert bbox(cavity_alpha) != bbox(lip_alpha), "bbox 相同就是复制品，不是口内"


def test_cavity_colour_is_warmer_than_the_lip_line(tmp_path):
    path = _write_mouth(tmp_path, lambda d: d.ellipse((20, 20, 140, 60), fill=(40, 30, 32, 255)))
    cavity = LayerComposer()._synthesize_mouth_cavity({"mouth": {"path": str(path)}}, ["mouth"], 80, 160)
    assert cavity is not None
    mean = cavity[cavity[..., 3] > 0][:, :3].mean(axis=0)
    assert mean[0] > mean[2], "口内应偏暖红，而不是原样的墨线颜色"


def test_thin_closed_mouth_does_not_create_a_duplicate(tmp_path):
    # 闭口只有一条 2px 墨线：内缩后没有可信内部，应直接不生成
    path = _write_mouth(tmp_path, lambda d: d.line((20, 40, 140, 40), fill=(30, 20, 20, 255), width=2))
    cavity = LayerComposer()._synthesize_mouth_cavity({"mouth": {"path": str(path)}}, ["mouth"], 80, 160)
    assert cavity is None


def test_thin_closed_mouth_gets_separate_inferred_open_expression(tmp_path):
    size = 100
    face = np.zeros((size, size, 4), dtype=np.uint8)
    face[10:90, 10:90] = (246, 218, 198, 255)
    mouth = np.zeros_like(face)
    mouth[55:57, 45:55] = (45, 26, 28, 255)
    Image.fromarray(face).save(tmp_path / "face.png")
    Image.fromarray(mouth).save(tmp_path / "mouth.png")
    layers = {
        "face": {"path": str(tmp_path / "face.png")},
        "mouth": {"path": str(tmp_path / "mouth.png")},
    }

    made = LayerComposer().synthesize_face_geometry(layers, size, size, tmp_path)

    assert "mouth_open" in made
    assert layers["mouth"]["path"].endswith("mouth.png")
    assert layers["mouth_open"]["rest_opacity"] == 0.0
    assert layers["mouth_open"]["provenance"] == "inferred_expression"
    open_layer = np.asarray(Image.open(layers["mouth_open"]["path"]).convert("RGBA"))
    assert int((open_layer[..., 3] > 0).sum()) > int((mouth[..., 3] > 0).sum())
    assert open_layer[..., 3].max() == 255


def test_small_unannotated_mouth_does_not_invent_teeth_or_tongue(tmp_path):
    size = 100
    face = np.zeros((size, size, 4), dtype=np.uint8)
    face[10:90, 10:90] = (246, 218, 198, 255)
    mouth = np.zeros_like(face)
    mouth[55:57, 45:55] = (45, 26, 28, 255)
    Image.fromarray(face).save(tmp_path / "face.png")
    Image.fromarray(mouth).save(tmp_path / "mouth.png")
    layers = {
        "face": {"path": str(tmp_path / "face.png")},
        "mouth": {"path": str(tmp_path / "mouth.png")},
    }

    LayerComposer().synthesize_face_geometry(layers, size, size, tmp_path)

    assert "teeth" not in layers
    assert "tongue" not in layers


def test_open_mouth_aperture_is_below_original_lip_line_and_inside_face():
    composer = LayerComposer()
    face = np.zeros((40, 50, 4), dtype=np.uint8)
    face[5:35, 5:45] = (246, 218, 198, 255)
    mouth = np.zeros_like(face)
    mouth[18:20, 23:27] = (45, 26, 28, 255)

    opened = composer._synthesize_open_mouth(face, mouth)

    assert opened is not None
    mask = opened[..., 3] > 0
    assert mask.sum() > 0
    ys, _ = np.where(mask)
    lip_y = np.where(mouth[..., 3] > 0)[0].max()
    assert ys.max() > lip_y
    assert not mask[:5].any() and not mask[35:].any()
    assert not mask[:, :5].any() and not mask[:, 45:].any()
