"""P0 rig-ready 分层的离线分辨率回归（标准画布 / 4K 高度画布）。

不加载任何模型、不启动 VTube Studio：用合成 mask 走
``LayerComposer → layer_manifest → 分组 PSD → 重新读取对账``。

为什么要有这条：manifest 与 PSD 的分组/顺序/坐标只在真实尺寸下才会暴露
问题（裁剪坐标越界、分组名编码、层序错位）。单元测试里 8×8 的 fixture
证明不了 1024 与 2160px 高度下还能一一对上。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.psd.creator import PSDCreator
from core.psd.validator import validate_psd_roundtrip
from core.segment_engine.composer import LayerComposer
from core.segment_engine.layer_manifest import (
    SOURCE_SEMANTIC,
    build_manifest,
    validate_manifest,
    write_manifest,
)

#: (width, height)。2160 是 4K 高度；宽度按常见 2:3 立绘比例等比给出。
CANVASES = [(1024, 1024), (1440, 2160)]


def _rect(h: int, w: int, fx0: float, fx1: float, fy0: float, fy1: float) -> np.ndarray:
    mask = np.zeros((h, w), dtype=bool)
    mask[int(fy0 * h):int(fy1 * h), int(fx0 * w):int(fx1 * w)] = True
    return mask


def _fixture(w: int, h: int):
    """一套左右齐全、互不重叠的 P0 部件（间隔 ≥1% 画布，避免硬切遮挡）。"""
    masks = {
        "hair_back": _rect(h, w, 0.20, 0.80, 0.01, 0.17),
        "hair_front": _rect(h, w, 0.30, 0.70, 0.19, 0.24),
        "face": _rect(h, w, 0.32, 0.68, 0.26, 0.40),
        # 眉毛/眼睛各给两块，交给 split_limb_parts / split_eye_parts 拆左右。
        "eyebrows": (_rect(h, w, 0.35, 0.44, 0.28, 0.295)
                     | _rect(h, w, 0.56, 0.65, 0.28, 0.295)),
        "eyes_left": _rect(h, w, 0.35, 0.44, 0.31, 0.345),
        "eyes_right": _rect(h, w, 0.56, 0.65, 0.31, 0.345),
        "mouth": _rect(h, w, 0.47, 0.53, 0.365, 0.38),
        "neck": _rect(h, w, 0.44, 0.56, 0.42, 0.45),
        "clothes_top": _rect(h, w, 0.28, 0.72, 0.47, 0.62),
        "arms": (_rect(h, w, 0.18, 0.24, 0.47, 0.66)
                 | _rect(h, w, 0.76, 0.82, 0.47, 0.66)),
        "hands": (_rect(h, w, 0.18, 0.24, 0.68, 0.72)
                  | _rect(h, w, 0.76, 0.82, 0.68, 0.72)),
        "legs": (_rect(h, w, 0.38, 0.44, 0.74, 0.98)
                 | _rect(h, w, 0.56, 0.62, 0.74, 0.98)),
    }

    arr = np.zeros((h, w, 4), dtype=np.uint8)
    palette = {
        "hair_back": (70, 40, 90, 255),
        "hair_front": (110, 80, 140, 255),
        "face": (235, 200, 175, 255),
        "eyebrows": (40, 30, 30, 255),
        "mouth": (170, 70, 80, 255),
        "neck": (225, 190, 165, 255),
        "clothes_top": (40, 110, 170, 255),
        "arms": (235, 200, 175, 255),
        "hands": (235, 200, 175, 255),
        "legs": (235, 200, 175, 255),
    }
    for name, color in palette.items():
        arr[masks[name]] = color
    # 眼睛一半暗一半亮：split_eye_parts 靠明暗差拆眼白/瞳孔。
    for name in ("eyes_left", "eyes_right"):
        eye = masks[name]
        ys, xs = np.where(eye)
        split = int((xs.min() + xs.max()) / 2)
        eye_half = eye & (np.arange(w)[None, :] <= split)
        arr[eye_half] = (30, 30, 40, 255)
        arr[eye & ~eye_half] = (240, 240, 245, 255)
    return masks, Image.fromarray(arr, "RGBA")


@pytest.mark.parametrize("width,height", CANVASES)
def test_p0_layering_manifest_and_psd_roundtrip(tmp_path: Path, width: int, height: int):
    masks, image = _fixture(width, height)
    layers_dir = tmp_path / "layers"

    composed = LayerComposer(device="cpu").compose(masks, image, str(layers_dir))

    # 合成器只负责几何；来源/置信度由检测层（这里是 fixture 的输入 mask）提供。
    export_layers = []
    for name, info in composed.items():
        entry = dict(info)
        entry["provenance"] = entry.get("provenance") or "detector"
        entry["status"] = entry.get("status") or "detected"
        entry["confidence"] = entry.get("confidence") or 0.9
        entry["source_mask"] = entry.get("source_mask", name)
        export_layers.append(entry)

    manifest = build_manifest(
        export_layers, source=SOURCE_SEMANTIC, canvas=(width, height))
    manifest_path = write_manifest(str(layers_dir), manifest)
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert validate_manifest(data) == []
    assert data["canvas"] == {"width": width, "height": height}
    # 分层齐全 + 每层都有证据：P0 必须真的过闸，而不是永远红灯。
    assert manifest.rig_ready is True, manifest.missing_required
    assert manifest.missing_required == []
    assert manifest.manual_required == []

    result = PSDCreator().create_psd(
        str(layers_dir), str(layers_dir / "character.psd"),
        manifest_path=manifest_path)

    assert result["success"] is True, result.get("error")
    layer_files = [layers_dir / entry["file"] for entry in data["layers"]]
    checked = validate_psd_roundtrip(
        result["psd_path"], data, layer_files=layer_files)

    assert checked["valid"] is True, checked["errors"]
    assert result["layer_count"] == len(data["layers"])
