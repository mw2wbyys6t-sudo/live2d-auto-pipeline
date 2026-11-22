"""图集坐标与导出一致性验收（M3 / INV-M3-3）。

用真实的 TextureAtlas 打包结果做正例，再逐项篡改元数据构造负例，
证明校验器真的能抓到「UV 算错 / 矩形重叠 / bbox 不一致 / 顶点 uv 篡改」。
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from live2d_builder.exporter.atlas_consistency import (
    verify_atlas_layout,
    verify_atlas_pixels,
    verify_model_uvs,
)
from live2d_builder.exporter.model3_exporter import Model3Exporter
from live2d_builder.exporter.moc3_pipeline import uv_for_vertex
from live2d_builder.exporter.texture_atlas import TextureAtlas


def _layer(w: int, h: int, colour, box=None) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    x0, y0, x1, y1 = box or (0, 0, w, h)
    arr[y0:y1, x0:x1] = colour
    return Image.fromarray(arr, "RGBA")


@pytest.fixture()
def packed():
    layers = {
        "hair": _layer(120, 160, (80, 80, 200, 255), (10, 20, 100, 140)),
        "face": _layer(120, 160, (255, 220, 180, 255), (30, 40, 90, 120)),
        "eye": _layer(40, 40, (40, 40, 60, 255), (5, 5, 30, 30)),
    }
    atlas = TextureAtlas(max_size=1024, padding=2)
    result = atlas.pack(layers)
    return layers, result


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------

def test_real_pack_passes_layout_and_pixel_checks(packed) -> None:
    layers, result = packed

    layout = verify_atlas_layout(result)
    pixels = verify_atlas_pixels(result, layers)

    assert layout["available"] is True and layout["ok"] is True, layout["errors"]
    assert pixels["available"] is True and pixels["ok"] is True, pixels["errors"]
    assert layout["checked"] == 3
    assert pixels["checked"] == 3


def test_uv_fields_are_mutually_derivable(packed) -> None:
    _layers, result = packed
    rect = result["uvs"]["hair"]

    assert rect["u0"] * rect["atlas_width"] == pytest.approx(rect["top_left"][0])
    assert (rect["u1"] - rect["u0"]) * rect["atlas_width"] == pytest.approx(rect["bbox"][2])


# ---------------------------------------------------------------------------
# 负例：UV 算错
# ---------------------------------------------------------------------------

def test_wrong_u0_is_caught(packed) -> None:
    _layers, result = packed
    result["uvs"]["hair"]["u0"] += 0.05

    report = verify_atlas_layout(result)

    assert report["ok"] is False
    assert any("u0*page_w" in e for e in report["errors"])


def test_out_of_range_uv_is_caught(packed) -> None:
    _layers, result = packed
    result["uvs"]["eye"]["u1"] = 1.4

    report = verify_atlas_layout(result)

    assert report["ok"] is False
    assert any("越界" in e for e in report["errors"])


def test_bbox_size_mismatch_is_caught(packed) -> None:
    _layers, result = packed
    result["uvs"]["face"]["bbox"] = (
        result["uvs"]["face"]["bbox"][0],
        result["uvs"]["face"]["bbox"][1],
        result["uvs"]["face"]["bbox"][2] + 7,
        result["uvs"]["face"]["bbox"][3],
    )

    report = verify_atlas_layout(result)

    assert report["ok"] is False
    assert any("bbox 宽" in e or "UV 宽度" in e for e in report["errors"])


# ---------------------------------------------------------------------------
# 负例：同页重叠
# ---------------------------------------------------------------------------

def test_overlapping_rects_are_caught(packed) -> None:
    _layers, result = packed
    source = result["uvs"]["hair"]
    # 把眼睛的矩形整体挪到头发正上方 -> 必然重叠。
    target = result["uvs"]["eye"]
    target["page"] = source["page"]
    target["top_left"] = source["top_left"]
    width = target["bbox"][2]
    height = target["bbox"][3]
    target["bottom_right"] = (source["top_left"][0] + width,
                              source["top_left"][1] + height)
    target["atlas_width"] = source["atlas_width"]
    target["atlas_height"] = source["atlas_height"]
    target["u0"] = target["top_left"][0] / target["atlas_width"]
    target["v0"] = target["top_left"][1] / target["atlas_height"]
    target["u1"] = target["bottom_right"][0] / target["atlas_width"]
    target["v1"] = target["bottom_right"][1] / target["atlas_height"]

    report = verify_atlas_layout(result)

    assert report["ok"] is False
    assert any("重叠" in e for e in report["errors"])


# ---------------------------------------------------------------------------
# 负例：像素回采不一致
# ---------------------------------------------------------------------------

def test_pixel_mismatch_is_caught(packed) -> None:
    layers, result = packed
    tampered = dict(layers)
    tampered["face"] = _layer(120, 160, (0, 255, 0, 255), (30, 40, 90, 120))

    report = verify_atlas_pixels(result, tampered)

    assert report["ok"] is False
    assert any("face" in e and "不一致" in e for e in report["errors"])


# ---------------------------------------------------------------------------
# 负例：模型顶点 uv 被篡改
# ---------------------------------------------------------------------------

def _model_for(result, name: str, verts):
    rect = result["uvs"][name]
    uvs = [list(uv_for_vertex(rect, float(x), float(y),
                              rect["bbox"][2], rect["bbox"][3]))
           for x, y in verts]
    return {"meshes": [{
        "name": name,
        "vertices": verts,
        "uvs": uvs,
        "width": rect["bbox"][2],
        "height": rect["bbox"][3],
    }]}


def test_model_uvs_derived_from_atlas_pass(packed) -> None:
    _layers, result = packed
    verts = [(40.0, 50.0), (80.0, 50.0), (80.0, 110.0), (40.0, 110.0)]

    report = verify_model_uvs(_model_for(result, "hair", verts), result["uvs"])

    assert report["available"] is True and report["ok"] is True, report["errors"]
    assert report["checked"] == 4


def test_tampered_model_uv_is_caught(packed) -> None:
    _layers, result = packed
    verts = [(40.0, 50.0), (80.0, 50.0)]
    model = _model_for(result, "hair", verts)
    model["meshes"][0]["uvs"][1][0] += 0.1

    report = verify_model_uvs(model, result["uvs"])

    assert report["ok"] is False
    assert any("推导值" in e for e in report["errors"])


def test_uv_count_mismatch_is_caught(packed) -> None:
    _layers, result = packed
    model = _model_for(result, "hair", [(40.0, 50.0), (80.0, 50.0)])
    model["meshes"][0]["uvs"].pop()

    report = verify_model_uvs(model, result["uvs"])

    assert report["ok"] is False
    assert any("数量不一致" in e for e in report["errors"])


# ---------------------------------------------------------------------------
# 输入不完整：必须「未验证」而不是通过
# ---------------------------------------------------------------------------

def test_missing_inputs_report_unverified_not_pass() -> None:
    assert verify_atlas_layout({})["ok"] is None
    assert verify_atlas_pixels({}, {})["ok"] is None
    assert verify_model_uvs({}, {})["ok"] is None


def test_export_blocks_atlas_failure_before_texture_write(tmp_path, monkeypatch) -> None:
    exporter = Model3Exporter()
    layer = _layer(8, 8, (10, 20, 30, 255))
    monkeypatch.setattr(
        "live2d_builder.exporter.model3_exporter.verify_atlas_layout",
        lambda _result: {"available": True, "ok": False, "errors": ["bad layout"]},
        raising=False,
    )

    with pytest.raises(ValueError, match="atlas layout"):
        exporter._export_textures({"face": layer}, tmp_path, "gate")

    assert not list(tmp_path.glob("*.texture_*.png"))


def test_export_blocks_when_layers_are_missing(tmp_path) -> None:
    exporter = Model3Exporter()

    with pytest.raises(ValueError, match="没有图层"):
        exporter._export_textures({}, tmp_path, "gate")

    assert not list(tmp_path.glob("*.texture_*.png"))


def test_export_blocks_unverified_atlas_pixels_before_texture_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "live2d_builder.exporter.model3_exporter.verify_atlas_layout",
        lambda _result: {"available": True, "ok": True, "errors": []},
        raising=False,
    )
    monkeypatch.setattr(
        "live2d_builder.exporter.model3_exporter.verify_atlas_pixels",
        lambda _result, _layers: {"available": False, "ok": None, "errors": ["missing"]},
        raising=False,
    )
    exporter = Model3Exporter()

    with pytest.raises(ValueError, match="atlas pixels"):
        exporter._export_textures({"face": _layer(8, 8, (10, 20, 30, 255))},
                                  tmp_path, "gate")

    assert not list(tmp_path.glob("*.texture_*.png"))


def test_export_blocks_pixel_checker_skips(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "live2d_builder.exporter.model3_exporter.verify_atlas_pixels",
        lambda _result, _layers: {"available": True, "ok": True, "errors": [],
                                  "skipped": ["large: resampled"]},
        raising=False,
    )
    exporter = Model3Exporter()

    with pytest.raises(ValueError, match="atlas pixels 未验证"):
        exporter._export_textures({"large": _layer(8, 8, (10, 20, 30, 255))},
                                  tmp_path, "gate")

    assert not list(tmp_path.glob("*.texture_*.png"))


def test_export_blocks_vertex_uv_failure_before_model3_write(tmp_path, monkeypatch) -> None:
    from live2d_builder.exporter import model3_exporter as module

    layer = _layer(8, 8, (10, 20, 30, 255))
    monkeypatch.setattr(module, "verify_atlas_layout",
                        lambda _result: {"available": True, "ok": True, "errors": []},
                        raising=False)
    monkeypatch.setattr(module, "verify_atlas_pixels",
                        lambda _result, _layers: {"available": True, "ok": True, "errors": []},
                        raising=False)
    monkeypatch.setattr(module, "verify_model_uvs",
                        lambda *_args, **_kwargs: {"available": True, "ok": False,
                                                   "errors": ["bad vertex uv"]},
                        raising=False)
    monkeypatch.setattr(exporter := Model3Exporter(), "_export_textures",
                        lambda *_args: ["gate.texture_00.png"])

    with pytest.raises(ValueError, match="model vertex UV"):
        exporter.export({"layers": {"face": layer}, "meshes": {}},
                        str(tmp_path), "gate")

    assert not (tmp_path / "gate.model3.json").exists()
