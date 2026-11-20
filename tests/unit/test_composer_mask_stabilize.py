"""合成前的掩码收拾：接碎片、丢空层、豁免五官。"""
import numpy as np
import pytest
from PIL import Image

from core.segment_engine.composer import LayerComposer


@pytest.fixture()
def composer():
    return LayerComposer(device="cpu")


def _canvas(h=768, w=512):
    return np.zeros((h, w), dtype=bool)


def test_near_empty_layer_is_dropped(composer):
    """实测残留过 74px 的 skirt_hem：看不见，却白占一次 draw call。"""
    masks = {"skirt_hem": _canvas(), "clothes_bottom": _canvas()}
    masks["skirt_hem"][100:105, 100:125] = True      # 约 125px
    masks["clothes_bottom"][300:500, 100:400] = True
    out = composer._stabilize_masks(masks, (768, 512))
    assert "skirt_hem" not in out
    assert "clothes_bottom" in out


def test_face_feature_is_exempt_from_area_floor(composer):
    """鼻子只有十几个像素，任何最小面积规则都会误杀它。"""
    nose = _canvas()
    nose[200:203, 240:245] = True                    # 15px
    out = composer._stabilize_masks({"nose": nose}, (768, 512))
    assert "nose" in out
    assert int(out["nose"].sum()) == 15


@pytest.mark.parametrize("name", ["hand_L", "hand_R", "eyebrow_L", "eyebrow_R"])
def test_required_rig_parts_are_not_dropped_by_full_canvas_area_floor(composer, name):
    mask = _canvas(h=4096, w=2731)
    mask[100:120, 100:120] = True

    out = composer._stabilize_masks({name: mask}, (4096, 2731))

    assert name in out
    assert int(out[name].sum()) >= 400


def test_closing_does_not_grow_back_layer_into_front_layer(composer):
    """缝合不能把后排层长进前排层，否则会伪造出「整件上衣挡住脖子」。

    实测真实立绘：缝合前 neck 838px 与上衣零重叠；闭运算把它撑到 960px、
    与上衣重叠 152px，遮挡图随即把整件上衣当成脖子遮挡区（76 倍），补画被
    守卫拦下，脖子最终被记成缺件。
    """
    neck = _canvas(h=120, w=120)
    neck[20:28, 50:70] = True
    shirt = _canvas(h=120, w=120)
    shirt[29:118, 10:110] = True          # 与脖子只隔 1px：闭运算会跨过去

    out = composer._stabilize_masks(
        {"neck": neck, "clothes_top": shirt}, (120, 120))

    assert not (out["neck"] & out["clothes_top"]).any(), "缝合造出了假重叠"
    assert int(out["neck"].sum()) >= int(neck.sum())


def test_one_pixel_gap_is_bridged(composer):
    """SAM 把同一条缎带切成相距 1px 的两块，导出后像被撕开。"""
    ribbon = _canvas()
    ribbon[100:160, 100:120] = True
    ribbon[100:160, 121:140] = True                  # 1px 竖缝
    before_components = _components(ribbon)
    out = composer._stabilize_masks({"accessories": ribbon}, (768, 512))
    assert before_components == 2
    assert _components(out["accessories"]) == 1


def test_skirt_partition_preserves_union_without_cross_layer_overlap(composer):
    masks = {
        "clothes_bottom": _canvas(),
        "skirt_hem_L": _canvas(),
        "skirt_hem_R": _canvas(),
        "waistband": _canvas(),
    }
    masks["clothes_bottom"][300:500, 100:400] = True
    masks["clothes_bottom"][301, 99] = True
    masks["skirt_hem_L"][500:520, 120:240] = True
    masks["skirt_hem_R"][500:520, 260:380] = True
    masks["waistband"][280:300, 150:350] = True
    original_union = np.logical_or.reduce(list(masks.values()))

    out = composer._stabilize_masks(masks, (768, 512))

    final_masks = [out[name] for name in masks]
    assert np.array_equal(np.logical_or.reduce(final_masks), original_union)
    assert all(not (left & right).any()
               for i, left in enumerate(final_masks)
               for right in final_masks[i + 1:])


def test_skirt_stabilization_preserves_small_original_garment_island(composer):
    masks = {
        "clothes_bottom": _canvas(),
        "skirt_inner": _canvas(),
        "skirt_hem_L": _canvas(),
        "skirt_hem_R": _canvas(),
        "waistband": _canvas(),
    }
    masks["clothes_bottom"][300:500, 100:400] = True
    masks["clothes_bottom"][100:105, 100:105] = True
    masks["skirt_inner"][330:470, 130:370] = True
    masks["skirt_hem_L"][500:520, 100:250] = True
    masks["skirt_hem_R"][500:520, 250:400] = True
    masks["waistband"][280:300, 100:400] = True
    original_union = np.logical_or.reduce(list(masks.values()))

    out = composer._stabilize_masks(masks, (768, 512))

    final_union = np.logical_or.reduce([out[name] for name in masks])
    assert np.array_equal(final_union, original_union)


def test_layer_that_never_completes_is_not_marked_manual_required(composer, tmp_path):
    """不做补画的层不能因为「前排部件比它大」被判成补画不可信。

    口内、袖口这类层从不补画，它们的像素全部来自原画。若膨胀比守卫对它们
    也生效，它们会被标成 manual_required，进而在分层清单里被算成缺件 ——
    必需部件清单会凭空多出几项。
    """
    image = Image.new("RGBA", (64, 64), (30, 40, 50, 255))
    mouth = np.zeros((64, 64), dtype=bool)
    mouth[30:32, 30:32] = True
    big = np.zeros((64, 64), dtype=bool)
    big[0:60, 0:60] = True

    layers = composer.compose({"mouth": mouth, "clothes_top": big}, image, str(tmp_path))

    assert layers["mouth"]["status"] is None
    assert layers["mouth"]["provenance"] is None
    assert layers["mouth"]["completed"] is False


def test_isolated_dust_is_removed(composer):
    """离主体很远的孤立小点不是部件，是噪声。"""
    mask = _canvas()
    mask[100:300, 100:300] = True                    # 主体
    mask[600:602, 450:452] = True                    # 4px 孤点
    out = composer._stabilize_masks({"hair_back": mask}, (768, 512))
    assert _components(out["hair_back"]) == 1
    assert not out["hair_back"][600, 450]


def _components(mask):
    import cv2
    num, _ = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    return num - 1
