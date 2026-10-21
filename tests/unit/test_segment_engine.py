"""Tests for semantic segmentation engine."""

import tempfile
from pathlib import Path

import pytest
import numpy as np
from PIL import Image

from core.segment_engine.kmeans import KMeansLayerer
from core.segment_engine.semantic import SemanticLayerer
from core.segment_engine.amodal import AmodalCompleter
from core.segment_engine.composer import LayerComposer


def make_test_character(width=256, height=256):
    """Create a simple test character image with distinct color regions."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    arr = np.array(img)

    # Hair (blue, top)
    arr[10:80, 60:196, :] = [100, 100, 255, 255]
    # Face (skin, center)
    arr[60:140, 80:176, :] = [255, 220, 180, 255]
    # Eyes (dark, on face)
    arr[80:95, 90:110, :] = [30, 30, 80, 255]
    arr[80:95, 146:166, :] = [30, 30, 80, 255]
    # Mouth (red)
    arr[115:125, 115:141, :] = [200, 50, 50, 255]
    # Clothes (green, bottom)
    arr[140:240, 50:206, :] = [50, 150, 50, 255]

    return Image.fromarray(arr, "RGBA")


class TestKMeansLayerer:
    """Test K-means clustering layerer."""

    def test_basic_layering(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=5)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            assert result["layer_count"] >= 3
            assert result["layers"]
            assert Path(result["output_dir"]).exists()

    def test_layer_files_exist(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=4)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            for layer in result["layers"]:
                assert Path(layer["path"]).exists()
                with Image.open(layer["path"]) as layer_img:
                    assert layer_img.mode == "RGBA"

    def test_preview_generated(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=4)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            assert Path(result["preview_path"]).exists()

    def test_guide_written(self):
        img = make_test_character()
        layerer = KMeansLayerer(k_clusters=4)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = layerer.layer(img, output_dir=tmpdir)
            assert Path(result["guide_path"]).exists()
            content = Path(result["guide_path"]).read_text()
            assert "K-Means" in content or "K-means" in content

    def test_empty_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        layerer = KMeansLayerer(k_clusters=5)
        result = layerer.layer(img, output_dir=tempfile.mkdtemp())
        assert result["layer_count"] == 0

    def test_single_color_image(self):
        img = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        layerer = KMeansLayerer(k_clusters=5)
        result = layerer.layer(img, output_dir=tempfile.mkdtemp())
        assert result["layer_count"] >= 1


class TestAmodalCompleter:
    """Test amodal completion / inpainting."""

    def test_cv2_inpainting(self):
        completer = AmodalCompleter()
        img = make_test_character()
        arr = np.array(img)
        mask = np.zeros(arr.shape[:2], dtype=np.uint8)
        mask[80:100, 100:150] = 255
        result = completer.complete(img, mask, mask)
        assert result is not None
        assert result.size == img.size

    def test_simple_fill(self):
        completer = AmodalCompleter()
        img = Image.new("RGB", (64, 64), color=(200, 100, 50))
        mask = np.zeros((64, 64), dtype=np.uint8)
        mask[20:40, 20:40] = 255
        result = completer._simple_fill(np.array(img), mask)
        assert result.shape == (64, 64, 3)


class TestLayerComposer:
    """Test layer composition and ordering."""

    def test_standard_order_exists(self):
        order = LayerComposer.STANDARD_LAYER_ORDER
        assert len(order) >= 18
        assert len(order) == len(set(order))
        assert "face_base" in order
        assert "hair_front" in order
        assert "hair_back" in order
        # 后发在脸后，**刘海在脸前**：分出来的脸在刘海位置是镂空，刘海若排在这里
        # (= 画在脸后)，闭眼图一补色就把刘海涂成皮肤（实测 1206 px）。
        assert order.index("hair_back") < order.index("face_base") < order.index("hair_front")
        # 闭眼图必须排在眼球**之前**（= 画在眼球下面）：闭眼时眼球整体向上收拢进
        # 上睫毛线，收拢后露出来的就是这张图。它画在眼球上面就永远看不见。
        assert order.index("eyelid_right") < order.index("irides_right")
        assert order.index("eyelid_right") < order.index("hair_front")
        assert order.index("hair_front") < order.index("eyebrow_R")
        # 口内必须在嘴线之前（先画腔体，再画嘴唇），否则张嘴时腔体盖住嘴唇。
        assert order.index("mouth_cavity") < order.index("mouth")
        for part in ("arm", "hand", "leg", "sleeve", "cuff", "sock", "boot"):
            assert f"{part}_L" in order
            assert f"{part}_R" in order
        assert order.index("clothes_top") < order.index("arm_L") < order.index("hand_L")
        assert order.index("bottomwear_back") < order.index("bottomwear_front")

    def test_compose_basic(self):
        composer = LayerComposer()
        img = make_test_character()
        arr = np.array(img)
        # Use non-overlapping masks for clean extraction
        h, w = arr.shape[:2]
        hair_mask = np.zeros((h, w), dtype=bool)
        hair_mask[10:80, 60:196] = True
        face_mask = np.zeros((h, w), dtype=bool)
        face_mask[60:140, 80:176] = True
        clothes_mask = np.zeros((h, w), dtype=bool)
        clothes_mask[140:240, 50:206] = True

        part_masks = {
            "hair_back": hair_mask,
            "face_base": face_mask,
            "clothes_top": clothes_mask,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = composer.compose(part_masks, img, tmpdir)
            # compose() returns a dict keyed by part name with metadata
            assert "hair_back" in result
            assert "face_base" in result
            assert "clothes_top" in result
            for name, info in result.items():
                assert "path" in info
                assert Path(info["path"]).exists()

    def test_generate_occlusion_map(self):
        composer = LayerComposer()
        masks = {
            "hair_front": np.zeros((64, 64), dtype=bool),
            "face_base": np.zeros((64, 64), dtype=bool),
        }
        masks["hair_front"][10:30, :] = True
        masks["face_base"][20:50, :] = True
        occ = composer.generate_occlusion_map(masks)
        assert isinstance(occ, dict)
        assert "hair_front" in occ
        assert "face_base" in occ

    def test_reorder_layers(self):
        composer = LayerComposer()
        # Create metadata dicts in arbitrary order
        layers = {
            "eyes": {"name": "eyes"},
            "hair_back": {"name": "hair_back"},
            "face_base": {"name": "face_base"},
            "hair_front": {"name": "hair_front"},
        }
        reordered = composer.reorder_layers(layers)
        keys = list(reordered.keys())
        # 列表越靠后 = 画得越靠前；头发在脸之前，脸在五官之前
        assert keys.index("hair_back") < keys.index("hair_front")
        assert keys.index("hair_back") < keys.index("eyes")

    def test_face_is_drawn_before_every_facial_feature(self):
        """脸底层必须排在五官之前。

        脸层现在是**干净皮肤**（见 LayerComposer.FACE_PARTS）：排在五官后面就会把
        眼睛整块盖掉——实测脸层还带着眼睛像素时顺序错也看不出来，清理干净后立刻
        露出眼睛被盖成碎片的画面。
        """
        order = LayerComposer.STANDARD_LAYER_ORDER
        face = order.index("face")
        assert face > order.index("face_base")
        for feature in ("eyebrows", "eyes", "eyewhite_left", "irides_left",
                        "eyelash_left", "nose", "mouth"):
            assert order.index(feature) > face, f"{feature} 被排在脸层之后"

    def test_face_layer_keeps_skin_where_the_mouth_is(self):
        """脸层里的五官像素必须补成肤色，且不能挖洞。

        历史缺陷：脸层直接复制原图像素，于是 `face.png` 与 `irides_left.png` 在瞳孔
        位置完全同色——眨眼时眼睛被压扁了，脸层又把瞳孔原样露出来，
        ParamEyeLOpen 改了跟没改一样。
        """
        arr = np.zeros((120, 120, 4), dtype=np.uint8)
        arr[20:110, 20:110] = (255, 220, 180, 255)   # 脸（肤色）
        arr[60:70, 50:80] = (60, 30, 30, 255)        # 嘴（深色墨线）
        img = Image.fromarray(arr, "RGBA")

        face = np.zeros((120, 120), dtype=bool)
        face[20:110, 20:110] = True
        mouth = np.zeros((120, 120), dtype=bool)
        mouth[60:70, 50:80] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            LayerComposer().compose({"face": face, "mouth": mouth}, img, tmpdir)
            face_arr = np.array(Image.open(Path(tmpdir) / "face.png").convert("RGBA"))
            mouth_arr = np.array(Image.open(Path(tmpdir) / "mouth.png").convert("RGBA"))

        center = face_arr[65, 65]
        assert center[3] == 255, "脸层被挖洞了：眨眼时会透出背景"
        assert center[0] > 200 and center[1] > 150, f"脸层仍留着嘴的像素: {center}"
        # 嘴层自己不受影响
        assert tuple(mouth_arr[65, 65]) == (60, 30, 30, 255)

    def test_generate_layer_json(self):
        composer = LayerComposer()
        layers = {
            "hair_back": {"name": "hair_back", "path": "/tmp/a.png", "bbox": (0,0,10,10),
                         "size": (10,10), "pixel_count": 100, "mean_color": (100,100,255),
                         "occluded_by": [], "completed": False},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = composer.generate_layer_json(layers, str(Path(tmpdir) / "layers.json"))
            assert Path(path).exists()
            import json
            data = json.loads(Path(path).read_text())
            assert "layers" in data
            assert data["layer_count"] == 1


class TestSynthesisedFaceGeometry:
    """合成上眼睑 / 口内：静止必须逐像素不变，闭眼张嘴必须真的盖得住。

    这两个图层在原始立绘里**不存在**（眼睛睁着、嘴闭着），但没有它们，眨眼只能把
    眼球压扁、张嘴只能把墨线撑粗——参数动了，表现却不像"闭眼/张嘴"。
    """

    SIZE = 160

    @classmethod
    def _character(cls):
        size = cls.SIZE
        arr = np.zeros((size, size, 4), dtype=np.uint8)
        arr[40:140, 40:120] = (255, 220, 180, 255)    # 脸
        arr[40:60, 50:110] = (70, 90, 200, 255)       # 刘海压在脸上：脸层这里要镂空
        arr[70:90, 55:75] = (40, 60, 120, 255)        # 左眼（虹膜）
        arr[70:90, 85:105] = (40, 60, 120, 255)       # 右眼（虹膜）
        # 上睫毛线：暗、且各通道接近 —— 闭眼图靠它认出"哪条是睫毛"（见
        # LayerComposer.EYELID_LASH_LUM / _SPREAD）。真实立绘都有这一条。
        arr[70:73, 55:75] = (30, 25, 30, 255)
        arr[70:73, 85:105] = (30, 25, 30, 255)
        arr[115:118, 74:86] = (40, 20, 24, 255)       # 嘴（墨线）
        img = Image.fromarray(arr, "RGBA")

        def box(x0, y0, x1, y1):
            mask = np.zeros((size, size), dtype=bool)
            mask[y0:y1, x0:x1] = True
            return mask

        face = box(40, 40, 120, 140) & ~box(50, 40, 110, 60)
        return img, {
            "face": face,
            "hair_front": box(50, 40, 110, 60),
            "irides_left": box(55, 70, 75, 90),
            "irides_right": box(85, 70, 105, 90),
            "mouth": box(74, 115, 86, 118),
        }

    @classmethod
    def _composite(cls, layers, order, respect_rest_opacity=True):
        canvas = np.zeros((cls.SIZE, cls.SIZE, 4), dtype=np.float64)
        for name in order:
            info = layers.get(name)
            if not info:
                continue
            if respect_rest_opacity and float(info.get("rest_opacity", 1.0)) <= 0.0:
                # 静止时不可见的层（腮红/眼泪）不参与静止画面
                continue
            src = np.array(Image.open(info["path"]).convert("RGBA")).astype(np.float64)
            alpha = src[..., 3:4] / 255.0
            canvas[..., :3] = src[..., :3] * alpha + canvas[..., :3] * (1 - alpha)
            canvas[..., 3:4] = alpha * 255.0 + canvas[..., 3:4] * (1 - alpha)
        return canvas

    def _compose(self, tmpdir):
        img, masks = self._character()
        composer = LayerComposer()
        layers = composer.compose(masks, img, tmpdir)
        order = list(composer.reorder_layers(layers).keys())
        return composer, layers, order

    def test_layers_are_synthesised(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _, layers, order = self._compose(tmpdir)
            for name in ("eyelid_left", "eyelid_right", "mouth_cavity"):
                assert layers[name].get("synthesized") is True
                assert Path(layers[name]["path"]).is_file()
        assert {"eyelid_left", "eyelid_right", "mouth_cavity"} <= set(layers)
        assert order.index("mouth_cavity") < order.index("mouth")
        # 闭眼图画在眼球**下面**（= 排在眼球之前）
        assert order.index("eyelid_left") < order.index("irides_left")
        # 腮红在脸皮上、五官之下；眼泪在眼睛之上
        assert order.index("face") < order.index("cheek") < order.index("irides_left")
        assert order.index("irides_left") < order.index("tear_left")

    def test_rest_pose_keeps_every_pixel(self):
        """**静止可见**的合成层不能改变任何一个像素。

        这是整条合成路线的前提：闭眼图的皮肤取自脸层原像素、睫毛逐字节复制眼球层
        自己的像素，口内与嘴唇轮廓完全重合。

        腮红/眼泪不在这条断言里 —— 它们静止时 ``rest_opacity=0``，压根不画
        （见 ``test_fade_in_layers_are_invisible_at_rest``）。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            _, layers, order = self._compose(tmpdir)
            added = [n for n, info in layers.items() if info.get("synthesized")]
            without = {k: v for k, v in layers.items() if k not in added}
            full = self._composite(layers, order)
            base = self._composite(without, [n for n in order if n in without])
        diff = np.abs(full - base).max()
        assert diff < 1.0, f"合成层改变了静止画面：最大差 {diff}"

    def test_fade_in_layers_are_invisible_at_rest(self):
        """腮红/眼泪必须标记为静止不可见，且真的不参与静止画面。

        它们是"淡入"型表情层：不透明度由参数键形驱动，静止姿态是 0。如果忘了标
        ``rest_opacity``，静止画面会凭空多出腮红和泪光（实测最大差 100.9）。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            _, layers, order = self._compose(tmpdir)
            for name in ("cheek", "tear_left", "tear_right"):
                assert name in layers, f"{name} 没有合成出来"
                assert float(layers[name]["rest_opacity"]) == 0.0, (
                    f"{name} 的 rest_opacity 不是 0，静止画面会被改动")
            # 有像素内容（不是空层），只是静止时透明
            for name in ("cheek", "tear_left", "tear_right"):
                arr = np.array(Image.open(layers[name]["path"]).convert("RGBA"))
                assert (arr[..., 3] > 0).any(), f"{name} 是空层，等于没做"
            # 尊重 rest_opacity 的合成结果 == 完全不含这些层的结果
            visible = self._composite(layers, order)
            without = self._composite(
                {k: v for k, v in layers.items()
                 if k not in ("cheek", "tear_left", "tear_right")}, order)
        diff = np.abs(visible - without).max()
        assert diff < 1.0, f"静止时仍有 {diff} 的差异，淡入层没被隐去"

    def test_closed_eye_image_is_opaque_where_the_eye_was(self):
        """闭眼图在眼睛原本不透明的地方必须**不透明**。

        闭眼时眼球向上收拢，空出来的地方要由闭眼图填住；那里要是透明的，
        就会露出后面的脸/头发，闭眼变成"眼睛被挖了个洞"。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            _, layers, _ = self._compose(tmpdir)
            lid = np.array(Image.open(layers["eyelid_left"]["path"]).convert("RGBA"))
            eye = np.array(Image.open(layers["irides_left"]["path"]).convert("RGBA"))
        eye_mask = eye[..., 3] > 8
        assert eye_mask.any()
        covered = (lid[..., 3] == 255) & eye_mask
        ratio = covered.sum() / eye_mask.sum()
        assert ratio > 0.95, f"闭眼图只盖住 {ratio:.0%} 的眼球区域"

    def test_closed_eye_image_keeps_the_original_lash_line(self):
        """闭眼图里的上睫毛线必须是原画自己的像素，不能是凭空造的。

        判据是逐字节相同：闭眼图取的就是眼球层的暗色描边行。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            _, layers, _ = self._compose(tmpdir)
            lid = np.array(Image.open(layers["eyelid_left"]["path"]).convert("RGBA"))
            eye = np.array(Image.open(layers["irides_left"]["path"]).convert("RGBA"))
        eye_solid = eye[..., 3] == 255
        assert eye_solid.any()
        # 闭眼图里那些"与眼球层逐字节相同"的像素就是保留下来的睫毛线
        same = np.all(lid[..., :3] == eye[..., :3], axis=2) & eye_solid
        assert same.sum() > 0, "闭眼图没有保留任何原画的睫毛像素"

    def test_closed_eye_image_is_filled_with_skin(self):
        """闭眼图的非睫毛区域必须是肤色，不能还是蓝虹膜。

        这条最关键：留了虹膜色就等于没闭眼，只是把眼睛缩小了。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            _, layers, _ = self._compose(tmpdir)
            lid = np.array(Image.open(layers["eyelid_left"]["path"]).convert("RGBA"))
            eye = np.array(Image.open(layers["irides_left"]["path"]).convert("RGBA"))
        lid_pixels = lid[..., 3] == 255
        assert lid_pixels.any()
        # 虹膜是蓝的（40,60,120）；闭眼图里蓝色通道不该压过红色通道
        rgb = lid[..., :3].astype(int)[lid_pixels]
        blue_dominant = int((rgb[:, 2] > rgb[:, 0]).sum())
        assert blue_dominant < len(rgb) * 0.2, (
            f"闭眼图还有 {blue_dominant}/{len(rgb)} 个蓝色像素（虹膜色没被皮肤盖住）")
        # 也不可能整张都是同一个暗色描边
        assert len(np.unique(rgb, axis=0)) > 1

    def test_mouth_cavity_hides_under_the_lip_and_is_warm(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _, layers, _ = self._compose(tmpdir)
            cavity = np.array(Image.open(layers["mouth_cavity"]["path"]).convert("RGBA"))
            mouth = np.array(Image.open(layers["mouth"]["path"]).convert("RGBA"))
        open_cavity = cavity[..., 3] > 0
        assert open_cavity.any()
        # 轮廓必须落在嘴唇里：否则静止时就露出一圈腔体
        assert not (open_cavity & ~(mouth[..., 3] > 0)).any()
        line = mouth[mouth[..., 3] > 0][:, :3].mean(axis=0)
        fill = cavity[open_cavity][:, :3].mean(axis=0)
        assert fill[0] - fill[2] > line[0] - line[2], (
            f"口内色不够暖：嘴线 {line} / 口内 {fill}")

    def test_synthesis_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            composer, layers, _ = self._compose(tmpdir)
            again = composer.synthesize_face_geometry(layers, self.SIZE, self.SIZE)
        assert again == []

    def test_no_face_layer_means_no_invented_lid(self):
        """没有脸层可取样时宁可不做，也不凭空画一块颜色。"""
        size = self.SIZE
        arr = np.zeros((size, size, 4), dtype=np.uint8)
        arr[70:90, 55:75] = (40, 60, 120, 255)
        img = Image.fromarray(arr, "RGBA")
        masks = {"irides_left": np.zeros((size, size), dtype=bool)}
        masks["irides_left"][70:90, 55:75] = True
        with tempfile.TemporaryDirectory() as tmpdir:
            layers = LayerComposer().compose(masks, img, tmpdir)
        assert "eyelid_left" not in layers


class TestForegroundGatedSegmentation:
    """rembg 的 ISNet 只输出一张前景掩码（label="foreground"），没有部件信息。

    历史行为把它丢给逐掩码分类，整张角色会被判成 hair（部件从 11 退化到 2 个
    完全重叠的 hair 层）。现在改为拿它当 gate 约束颜色分割，本测试锁定该契约。
    """

    @staticmethod
    def _segmenter(monkeypatch, fg, color_parts):
        seg = SemanticLayerer(device="cpu", model_type="isnet")
        monkeypatch.setattr(
            seg, "_segment_isnet",
            lambda image: [{"segmentation": fg, "label": "foreground"}])
        monkeypatch.setattr(
            seg, "_fallback_color_segment", lambda image, fg=None: color_parts)
        return seg

    def test_color_parts_are_clipped_to_foreground(self, monkeypatch):
        img = Image.new("RGB", (64, 64), (0, 0, 0))
        fg = np.zeros((64, 64), dtype=bool)
        fg[0:32, :] = True  # 上半为角色前景
        inside = np.zeros((64, 64), dtype=bool)
        inside[0:16, 0:16] = True
        outside = np.zeros((64, 64), dtype=bool)
        outside[48:64, 0:16] = True  # 落在背景里
        seg = self._segmenter(monkeypatch, fg, {"face": inside, "legs": outside})

        parts = seg.segment(img)

        assert seg._last_was_fg_gated is True
        assert seg._last_was_fallback is False
        assert "face" in parts and parts["face"].any()
        # 落在背景之外的部件应被前景裁掉
        assert "legs" not in parts

    def test_single_foreground_mask_skips_part_classifier(self, monkeypatch):
        img = Image.new("RGB", (64, 64), (0, 0, 0))
        fg = np.ones((64, 64), dtype=bool)
        seg = self._segmenter(monkeypatch, fg, {"face": fg.copy()})

        def _boom(*args, **kwargs):
            raise AssertionError("前景-only 掩码不应进入逐掩码分类")

        monkeypatch.setattr(seg, "_classify_masks_anime", _boom)

        assert seg.segment(img)  # 不抛异常即说明没有走逐掩码分类

    def test_layer_reports_fg_gated_method(self, monkeypatch, tmp_path):
        img = Image.new("RGB", (64, 64), (0, 0, 0))
        fg = np.ones((64, 64), dtype=bool)
        seg = self._segmenter(monkeypatch, fg, {"face": fg.copy()})

        result = seg.layer(img, output_dir=str(tmp_path))

        assert result["method"] == "semantic_fg_gated_color"


class TestSynthesisedSilhouette:
    """没有学习式前景模型、图也不带透明背景时，必须自估角色剪影。

    历史行为是 ``fg = 全 True``：立绘的深色网格墙与头发同色系，实测
    ``hair_back`` 覆盖 68% 画布、bbox 铺满全图，压在画面上的白色标注文字
    又被切进 ``clothes_top``，把上衣打成二百多个碎块。质量门因此报 7 条
    缺陷（超过门槛 4）。这里锁定"背景和文字都不能进前景"。
    """

    @staticmethod
    def _canvas(h=300, w=200):
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        rgb[...] = (45, 60, 82)          # 深色网格墙
        rgb[60:260, 70:130] = (40, 150, 210)  # 亮蓝角色，四边都不贴边
        rgb[40:46, 20:60] = 255          # 白色标注文字，飘在背景上
        return rgb

    def test_background_and_annotation_text_are_excluded(self):
        fg = SemanticLayerer()._synthesise_foreground(self._canvas())

        assert fg[150, 100], "角色主体必须留在前景里"
        assert not fg[10, 10], "画布角落的背景不能算前景"
        assert not fg[42, 30], "不与角色连通的标注文字不能算前景"

    def test_flat_canvas_falls_back_to_full_frame(self, caplog):
        fg = SemanticLayerer()._synthesise_foreground(
            np.full((100, 100, 3), 128, dtype=np.uint8)
        )

        assert fg.all(), "抓不出剪影时应整幅图兜底，而不是什么都不给"

    def test_alpha_is_trusted_only_when_actually_transparent(self):
        opaque = Image.new("RGBA", (20, 20), (10, 20, 30, 255))
        cutout = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        cutout.paste((10, 20, 30, 255), (5, 5, 15, 20))

        assert SemanticLayerer._alpha_carries_silhouette(opaque) is False
        assert SemanticLayerer._alpha_carries_silhouette(cutout) is True

    def test_segment_records_synthesised_silhouette(self):
        seg = SemanticLayerer(device="cpu", model_type="isnet")
        seg.segment(Image.fromarray(self._canvas(), "RGB"))

        assert seg._last_fg_synthesised is True
        assert seg._last_fg_source == "synthesised"
