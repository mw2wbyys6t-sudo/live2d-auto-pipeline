"""l2d_sam_iter2 后端接入 + 遮挡补全 的契约测试。

不加载 1.23 GB 权重、不跑 310s 推理：只验证

1. 依赖定位与失败面（缺源码/缺权重必须抛 :class:`ModelUnavailable`，不静默降级）；
2. 19 类名字进入 composer 的正确绘制顺序与补画名单；
3. ``layer()`` 复用合成器导出，层序 = 由后到前；
4. 大片遮挡走扩散补画，且颜色只从本层可见像素来。
"""

import numpy as np
import pytest
from PIL import Image

import core.segment_engine.l2d_sam as mod
from core.segment_engine.amodal import AmodalCompleter
from core.segment_engine.composer import LayerComposer
from core.segment_engine.layers52 import VALID_BODY_PARTS_V2
from core.segment_engine.l2d_sam import (
    CLASS_NUM,
    METHOD_L2D_SAM,
    L2dSamSegmenter,
    find_seethrough_common,
)
from core.segment_engine.sam2_gd import ModelUnavailable


# --------------------------------------------------------------------- backend


def _fake_common(root):
    """造一个最小 See-through common 目录（只满足存在性检查）。"""
    common = root / "see-through" / "common"
    (common / "modules").mkdir(parents=True)
    (common / "modules" / "__init__.py").write_text("", encoding="utf-8")
    (common / "modules" / "semanticsam.py").write_text("", encoding="utf-8")
    return common


def test_find_common_dir_honours_env(tmp_path, monkeypatch):
    common = _fake_common(tmp_path)
    monkeypatch.setenv("LIVE2D_SEETHROUGH_COMMON", str(common))
    assert find_seethrough_common() == common


def test_backend_covers_exactly_the_19_model_classes():
    assert CLASS_NUM == 19
    assert L2dSamSegmenter.STANDARD_PARTS == list(VALID_BODY_PARTS_V2)
    assert L2dSamSegmenter(device="cpu").method == METHOD_L2D_SAM


def test_load_fails_loudly_without_source(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "HAS_TORCH", True)
    missing = tmp_path / "nowhere"
    seg = L2dSamSegmenter(
        device="cpu", checkpoint=str(tmp_path / "x.pt"), common_dir=str(missing))
    with pytest.raises(ModelUnavailable, match="LIVE2D_SEETHROUGH_COMMON"):
        seg.load()


def test_load_fails_loudly_without_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "HAS_TORCH", True)
    common = _fake_common(tmp_path)
    seg = L2dSamSegmenter(
        device="cpu", checkpoint=str(tmp_path / "nope.pt"), common_dir=str(common))
    with pytest.raises(ModelUnavailable, match="权重"):
        seg.load()


def test_load_fails_loudly_without_torch(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "HAS_TORCH", False)
    seg = L2dSamSegmenter(device="cpu", checkpoint=str(tmp_path / "x.pt"),
                          common_dir=str(tmp_path))
    with pytest.raises(ModelUnavailable, match="torch"):
        seg.load()


def test_manifest_source_is_part_level_not_hsv_fallback():
    """layer_manifest 不能把 19 类模型结果标成纯颜色兜底。"""
    from core.segment_engine.layer_manifest import SOURCE_SEMANTIC, source_for_method

    assert source_for_method(METHOD_L2D_SAM) == SOURCE_SEMANTIC
    assert source_for_method("l2d_sam") == SOURCE_SEMANTIC


def test_segment_detailed_decodes_19_logit_channels():
    """不加载真权重，验证 logits -> 掩码/报告 的解码路径。"""
    torch = pytest.importorskip("torch")

    class _FakeModel:
        def inference(self, image):
            logits = torch.full((CLASS_NUM, 40, 60), -4.0)
            logits[0, 5:20, 10:30] = 4.0     # hair
            logits[11, 20:35, 30:50] = 2.0   # topwear
            return [logits]

    seg = L2dSamSegmenter(device="cpu", checkpoint="unused.pt")
    seg._loaded = True
    seg._model = _FakeModel()

    masks, report = seg.segment_detailed(Image.new("RGBA", (60, 40), (255, 255, 255, 255)))

    assert set(masks) == {"hair", "topwear"}
    assert masks["hair"].shape == (40, 60)
    assert masks["hair"][10, 15] and not masks["hair"][0, 0]
    assert report["method"] == METHOD_L2D_SAM
    assert report["detected_parts"] == ["hair", "topwear"]
    assert "bottomwear" in report["missing_parts"]
    assert report["mapping"]["topwear"]["primary"] == 21
    assert report["parts_report"]["hair"]["pixel_count"] == 15 * 20


def test_bfloat16_logits_are_downcast_before_numpy():
    """模型权重是 bfloat16；numpy 没有该 dtype，必须先转 float32。"""
    torch = pytest.importorskip("torch")

    class _FakeModel:
        def inference(self, image):
            logits = torch.full((CLASS_NUM, 8, 8), -4.0).to(torch.bfloat16)
            logits[0, 1:4, 1:4] = 4.0
            return [logits]

    seg = L2dSamSegmenter(device="cpu", checkpoint="unused.pt")
    seg._loaded = True
    seg._model = _FakeModel()
    masks, _ = seg.segment_detailed(Image.new("RGB", (8, 8), (0, 0, 0)))
    assert set(masks) == {"hair"}


def test_threshold_can_drop_low_confidence_parts():
    pytest.importorskip("torch")
    import torch

    class _FakeModel:
        def inference(self, image):
            logits = torch.full((CLASS_NUM, 8, 8), -4.0)
            logits[0, 1:4, 1:4] = 4.0    # p≈0.98
            logits[11, 5:7, 5:7] = 2.0   # p≈0.88
            return [logits]

    seg = L2dSamSegmenter(device="cpu", checkpoint="unused.pt", threshold=0.95)
    seg._loaded = True
    seg._model = _FakeModel()
    masks, _ = seg.segment_detailed(Image.new("RGB", (8, 8), (0, 0, 0)))
    assert set(masks) == {"hair"}


# ------------------------------------------------------------- composer order


def test_standard_order_contains_every_19_class_name():
    order = LayerComposer.STANDARD_LAYER_ORDER
    missing = [name for name in VALID_BODY_PARTS_V2 if name not in order]
    assert missing == [], f"这些 19 类名字没进绘制顺序: {missing}"
    assert len(order) == len(set(order))


def test_19_class_draw_order_is_anatomically_correct():
    idx = {n: i for i, n in enumerate(LayerComposer.STANDARD_LAYER_ORDER)}
    assert idx["hair"] < idx["face"]          # 头发在脸后面
    assert idx["topwear"] < idx["handwear"]   # 上衣被袖子/手套压住
    assert idx["legs"] < idx["bottomwear"]    # 下装压住裸腿
    assert idx["bottomwear"] < idx["legwear"] < idx["footwear"]
    assert idx["objects"] > idx["accessories"]  # 道具最前


def test_19_class_back_parts_are_amodal_candidates():
    for name in ("hair", "topwear", "bottomwear", "legwear", "neckwear"):
        assert name in LayerComposer.AMODAL_PARTS
    for name in ("handwear", "footwear", "objects"):
        assert name not in LayerComposer.AMODAL_PARTS


# ----------------------------------------------------------------- layer()


def _stub_segmenter(monkeypatch, masks):
    seg = L2dSamSegmenter(device="cpu", checkpoint="unused.pt")

    def fake_segment_detailed(image):
        report = {
            "method": METHOD_L2D_SAM,
            "missing_parts": [p for p in VALID_BODY_PARTS_V2 if p not in masks],
            "detected_parts": sorted(masks),
            "elapsed_s": 0.0,
        }
        return {k: v.copy() for k, v in masks.items()}, report

    monkeypatch.setattr(seg, "segment_detailed", fake_segment_detailed)
    return seg


def test_layer_exports_19_class_names_back_to_front(tmp_path, monkeypatch):
    h = w = 96
    hair = np.zeros((h, w), dtype=bool)
    hair[5:50, 5:90] = True
    face = np.zeros((h, w), dtype=bool)
    face[50:80, 30:70] = True          # 与 hair 相邻但不重叠（硬切）
    topwear = np.zeros((h, w), dtype=bool)
    topwear[80:95, 20:80] = True
    image = Image.new("RGBA", (w, h), (235, 235, 235, 255))

    seg = _stub_segmenter(monkeypatch, {"hair": hair, "face": face, "topwear": topwear})
    result = seg.layer(image, output_dir=str(tmp_path))

    assert result["success"] is True
    assert result["method"] == METHOD_L2D_SAM
    names = [layer["part_name"] for layer in result["layers"]]
    assert names.index("hair") < names.index("face") < names.index("topwear")

    hair_layer = next(l for l in result["layers"] if l["part_name"] == "hair")
    assert hair_layer["amodal_completed"] is True, (
        "头发是后排层，被脸压住时应该触发遮挡补画")
    assert (tmp_path / "preview.png").is_file()
    assert (tmp_path / "layer_guide.json").is_file()


def test_amodal_skips_absurd_expansion(tmp_path):
    """整件上衣被当成脖子的遮挡区时，宁可不补也不造出巨型假脖子。"""
    h = w = 120
    neck = np.zeros((h, w), dtype=bool)
    neck[20:28, 50:70] = True            # 160 px
    shirt = np.zeros((h, w), dtype=bool)
    shirt[25:118, 10:110] = True         # 与脖子有 3px 重叠 -> 走整块前排掩码

    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[neck] = (220, 170, 130, 255)
    arr[shirt] = (30, 100, 160, 255)

    layers = LayerComposer(device="cpu").compose(
        {"neck": neck, "topwear": shirt}, Image.fromarray(arr, "RGBA"), str(tmp_path))

    assert layers["neck"]["completed"] is False
    assert layers["neck"]["pixel_count"] == int(neck.sum())


def test_face_layer_erases_19_class_eye_pixels(tmp_path, monkeypatch):
    """脸层不许留着眼睛像素，否则眨眼时脸上还有一副睁开的眼。"""
    h = w = 60
    face = np.zeros((h, w), dtype=bool)
    face[10:50, 10:50] = True
    eyes = np.zeros((h, w), dtype=bool)
    eyes[20:28, 18:28] = True

    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[face] = (230, 190, 170, 255)   # 肤色
    arr[eyes] = (20, 30, 120, 255)     # 深蓝瞳孔

    seg = _stub_segmenter(monkeypatch, {"face": face, "eyes": eyes})
    result = seg.layer(Image.fromarray(arr, "RGBA"), output_dir=str(tmp_path))

    face_path = next(l["path"] for l in result["layers"] if l["part_name"] == "face")
    with Image.open(face_path) as layer:
        face_png = np.array(layer.convert("RGBA"))

    region = face_png[20:28, 18:28, :3].reshape(-1, 3).astype(np.float64)
    skin = np.array([230, 190, 170], dtype=np.float64)
    eye = np.array([20, 30, 120], dtype=np.float64)
    mean = region.mean(axis=0)
    assert np.linalg.norm(mean - skin) < np.linalg.norm(mean - eye), (
        f"脸层眼睛区域 {mean} 仍是瞳色，没被肤色补掉")


# --------------------------------------------------------------- amodal fill


def test_diffusion_fill_interpolates_between_boundary_colors():
    completer = AmodalCompleter()
    img = np.zeros((120, 120, 3), dtype=np.uint8)
    img[:, :30] = (200, 0, 0)
    img[:, 90:] = (0, 0, 200)
    hole = np.zeros((120, 120), dtype=bool)
    hole[30:90, 30:90] = True

    out = completer._diffusion_fill(img, hole)
    assert out.shape == img.shape and out.dtype == np.uint8

    left = out[30:90, 40].astype(np.int32)
    right = out[30:90, 80].astype(np.int32)
    assert left[:, 0].mean() > right[:, 0].mean()   # 红 -> 蓝的过渡方向
    assert right[:, 2].mean() > left[:, 2].mean()
    assert np.array_equal(out[~hole], img[~hole])    # 已知像素被钉死


def test_complete_routes_large_holes_to_diffusion(monkeypatch):
    completer = AmodalCompleter()
    seen = {}
    real = completer._diffusion_fill

    def spy(image, hole, *args, **kwargs):
        seen["pixels"] = int(np.asarray(hole).sum())
        return real(image, hole, *args, **kwargs)

    monkeypatch.setattr(completer, "_diffusion_fill", spy)

    image = Image.new("RGB", (160, 160), (180, 180, 180))
    visible = np.zeros((160, 160), dtype=bool)
    visible[10:60, 10:150] = True
    occluded = np.zeros((160, 160), dtype=bool)
    occluded[60:150, 20:140] = True

    out = completer.complete(image, visible, occluded)
    assert seen.get("pixels", 0) >= AmodalCompleter.LARGE_HOLE_PIXELS
    assert out.size == image.size
