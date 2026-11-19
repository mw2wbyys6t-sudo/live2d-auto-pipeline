"""拆分降级证据（SplitNote）与 amodal 补全可信度的契约测试。

对应 docs/staging/specs/2026-11-19-m2-p1-evidence.md 的 INV-A1 / A2 / A3：
拆分受限与补全推断必须从「日志」提升为「清单里可核对的结构化证据」。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.segment_engine.composer import LayerComposer
from core.segment_engine.layer_manifest import SOURCE_SEMANTIC, build_manifest
from core.segment_engine.part_split import (
    DEGRADATION_CODES,
    SplitNote,
    split_eye_parts,
    split_limb_parts,
)


# ---------------------------------------------------------------------------
# SplitNote 的向后兼容性：它必须先是一个能当 str 用的对象
# ---------------------------------------------------------------------------

def test_split_note_behaves_like_a_plain_string() -> None:
    note = SplitNote("arm 只检测到单侧（left），另一侧缺失", "limb_single_side", "arm")

    assert isinstance(note, str)
    assert note == "arm 只检测到单侧（left），另一侧缺失"
    assert "单侧" in note
    assert note.code == "limb_single_side"
    assert note.part == "arm"


def test_degradation_code_set_is_fixed_and_documented() -> None:
    assert DEGRADATION_CODES == {
        # A 阶段：既有拆分（肢体/眼/发/深度）的降级码
        "limb_no_midline",
        "limb_single_side",
        "eye_low_contrast",
        "eye_ratio_abnormal",
        "hair_front_back_failed",
        "depth_insufficient",
        "depth_ratio_unbalanced",
        # B 阶段：P1 眼/口细节在证据不足时的降级码
        "eye_detail_low_evidence",
        "lash_not_separable",
        "mouth_detail_low_evidence",
        "mouth_closed_line",
    }


# ---------------------------------------------------------------------------
# 拆分函数：降级路径必须带机器码
# ---------------------------------------------------------------------------

def test_eye_with_flat_luminance_reports_low_contrast_code() -> None:
    h, w = 40, 40
    eye = np.zeros((h, w), dtype=bool)
    eye[10:20, 10:30] = True
    image = np.full((h, w, 3), 128, dtype=np.uint8)  # 整块同亮度

    masks, warnings = split_eye_parts({"eyes_left": eye}, image)

    assert "eyes_left" in masks
    codes = {getattr(w_, "code", None) for w_ in warnings}
    assert "eye_low_contrast" in codes
    parts = {getattr(w_, "part", None) for w_ in warnings}
    assert "eyes_left" in parts


def test_one_sided_limb_reports_single_side_code() -> None:
    h, w = 40, 40
    # 一条竖线正好压在中线上：只能拆出单侧。
    arm = np.zeros((h, w), dtype=bool)
    arm[5:35, 20:21] = True

    _masks, warnings = split_limb_parts({"arm": arm}, (w, h))

    codes = {getattr(w_, "code", None) for w_ in warnings}
    assert "limb_single_side" in codes, f"got {warnings}"
    notes = [w_ for w_ in warnings if getattr(w_, "code", None) in DEGRADATION_CODES]
    assert notes and all(getattr(n, "part", None) == "arm" for n in notes)


def test_empty_limb_reports_no_midline_code() -> None:
    h, w = 40, 40

    masks, warnings = split_limb_parts({"arm": np.zeros((h, w), dtype=bool)}, (w, h))

    assert "arm" in masks
    codes = {getattr(w_, "code", None) for w_ in warnings}
    assert "limb_no_midline" in codes, f"got {warnings}"


# ---------------------------------------------------------------------------
# composer：降级原因必须随层写出
# ---------------------------------------------------------------------------

def _flat_eye_case() -> tuple[dict, Image.Image]:
    h, w = 60, 60
    eye = np.zeros((h, w), dtype=bool)
    eye[20:30, 20:40] = True
    img = Image.new("RGBA", (w, h), (128, 128, 128, 255))
    return {"eyes_left": eye}, img


def test_compose_attaches_split_evidence_to_the_affected_layer(tmp_path: Path) -> None:
    masks, img = _flat_eye_case()

    layers = LayerComposer(device="cpu").compose(masks, img, str(tmp_path))

    info = layers["eyes_left"]
    assert info["split_limited"] is True
    assert info["split_warnings"]
    assert "明暗差不足" in info["split_warnings"][0]


def test_compose_leaves_unaffected_layers_without_split_evidence(tmp_path: Path) -> None:
    h, w = 60, 60
    face = np.zeros((h, w), dtype=bool)
    face[5:55, 5:55] = True
    img = Image.new("RGBA", (w, h), (200, 180, 170, 255))

    layers = LayerComposer(device="cpu").compose({"face_base": face}, img, str(tmp_path))

    assert layers["face_base"]["split_limited"] is False
    assert layers["face_base"]["split_warnings"] == []


# ---------------------------------------------------------------------------
# manifest：降级可见、阻断高质量、但不改可导出
# ---------------------------------------------------------------------------

RIG_COMPLETE = [
    "hair_back", "hair_bangs", "face", "eyebrow_L", "eyebrow_R",
    "eyes_L", "eyes_R", "mouth", "neck", "torso", "clothes_top",
    "arm_L", "arm_R", "hand_L", "hand_R", "leg_L", "leg_R",
]


def _layer(name: str) -> dict:
    return {
        "name": name,
        "path": f"/tmp/out/{name}.png",
        "pixel_count": 1000,
        "bbox": [0, 0, 10, 10],
        "provenance": "detector",
        "confidence": 0.95,
    }


def test_split_limited_blocks_quality_but_not_export_readiness() -> None:
    clean = build_manifest([_layer(n) for n in RIG_COMPLETE],
                           source=SOURCE_SEMANTIC, canvas=(1024, 1024))
    degraded_layers = [_layer(n) for n in RIG_COMPLETE]
    degraded_layers[0]["split_limited"] = True
    degraded_layers[0]["split_warnings"] = ["hair_back 深度差不足，未拆前后"]
    degraded = build_manifest(degraded_layers,
                              source=SOURCE_SEMANTIC, canvas=(1024, 1024))

    assert degraded.rig_ready == clean.rig_ready, "可导出性不应被拆分降级改变"
    assert degraded.quality_ready is False
    assert any("拆分降级" in w for w in degraded.warnings)


def test_split_evidence_is_written_per_layer() -> None:
    layers = [_layer(n) for n in RIG_COMPLETE]
    layers[0]["split_limited"] = True
    layers[0]["split_warnings"] = ["hair_back 深度差不足，未拆前后"]

    data = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(1024, 1024)).to_dict()
    entry = next(e for e in data["layers"] if e["id"] == "hair_back")

    assert entry["split_limited"] is True
    assert entry["split_warnings"] == ["hair_back 深度差不足，未拆前后"]


def test_completed_layer_without_completion_evidence_warns_and_blocks_quality() -> None:
    layers = [_layer(n) for n in RIG_COMPLETE]
    for layer in layers:
        layer["status"] = "completed"
        layer["provenance"] = "amodal_completion"
        layer["completed_pixels"] = 200

    manifest = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(1024, 1024))

    assert any("缺少方法/可信度证据" in w for w in manifest.warnings)
    assert manifest.quality_ready is False


def test_completed_layer_with_evidence_records_method_and_confidence() -> None:
    layers = [_layer(n) for n in RIG_COMPLETE]
    for layer in layers:
        layer["status"] = "completed"
        layer["provenance"] = "amodal_completion"
        layer["completed_pixels"] = 200
        layer["completion_method"] = "telea"
        layer["completion_confidence"] = 0.6

    data = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(1024, 1024)).to_dict()
    entry = data["layers"][0]

    assert entry["completion_method"] == "telea"
    assert entry["completion_confidence"] == pytest.approx(0.6)
    assert not any("缺少方法/可信度证据" in w for w in data["warnings"])


def test_mouth_slot_is_satisfied_by_split_lips() -> None:
    """口部细节拆分后母层 mouth 不存在，上下唇齐全即视为嘴部齐全。

    否则每张露齿的图都会被误判成「缺嘴」，丢掉预览/导出资格。
    """
    names = [n for n in RIG_COMPLETE if n != "mouth"]
    names += ["lip_upper", "lip_lower"]

    manifest = build_manifest([_layer(n) for n in names],
                              source=SOURCE_SEMANTIC, canvas=(1024, 1024))

    assert "mouth" not in manifest.missing_preview
    assert manifest.preview_ready is True