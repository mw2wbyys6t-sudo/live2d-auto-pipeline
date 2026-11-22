from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.segment_engine.composer import LayerComposer
from core.segment_engine.layer_manifest import (
    MANIFEST_FILENAME,
    REQUIRED_FOR_PREVIEW,
    SCHEMA_VERSION,
    SOURCE_COLOR_CLUSTER,
    SOURCE_SEMANTIC,
    build_manifest,
    is_hollow,
    load_manifest,
    resolve_part,
    validate_manifest,
    write_manifest,
)


def test_synthesized_exemption_names_are_in_standard_layer_order():
    exempt = set(LayerComposer.synthesized_overlay_sources())
    exempt.update(LayerComposer.REST_INVISIBLE_PARTS)
    assert exempt <= set(LayerComposer.STANDARD_LAYER_ORDER)


def test_open_mouth_is_ordered_after_its_inferred_cavity_and_before_lips():
    order = LayerComposer.STANDARD_LAYER_ORDER
    assert order.index("mouth_cavity") < order.index("mouth_open") < order.index("mouth")


def test_eyebrows_without_side_do_not_satisfy_left_and_right_required_slots():
    manifest = build_manifest(
        [_layer("eyebrows")], source=SOURCE_SEMANTIC, canvas=(1024, 1536))

    assert "eyebrow:L" in manifest.missing_required
    assert "eyebrow:R" in manifest.missing_required


def test_synthesized_layers_are_registered_in_the_head_face_group():
    """闭眼图/眼泪/口内必须在清单里登记，不能落进 accessory 组。"""
    for name in ("eyelid_left", "tear_left", "mouth_cavity", "mouth_open"):
        data = build_manifest(
            [_layer(name)], source=SOURCE_SEMANTIC, canvas=(1024, 1536)).to_dict()
        entry = data["layers"][0]
        assert entry["group"] == "head_face"
        assert not any("未识别部件" in w for w in data["warnings"])


def test_eyebrows_are_not_applicable_when_source_has_no_brow_strokes():
    """源图确实没有眉毛笔画时按 N/A 处理：不算缺件，但证据要写清。"""
    evidence = {
        "absent": True,
        "sides": {"L": {"pixels": 0}, "R": {"pixels": 0}},
        "reason": "源图眉部窗口内没有可分离的眉毛笔画",
    }
    manifest = build_manifest(
        [_layer("face"), _layer("hair_back")],
        source=SOURCE_SEMANTIC,
        canvas=(1024, 1536),
        eyebrow_evidence=evidence,
    )

    assert set(("eyebrow:L", "eyebrow:R")) <= set(manifest.not_applicable)
    assert "eyebrow:L" not in manifest.missing_required
    assert "eyebrow:R" not in manifest.missing_required
    assert any("不适用" in w for w in manifest.warnings)
    assert "not_applicable" in manifest.to_dict()


def test_eyebrows_stay_missing_without_absence_evidence():
    """没有「源图无眉毛」证据时，眉毛缺件必须照旧报出来，不能被 N/A 吞掉。"""
    manifest = build_manifest(
        [_layer("face"), _layer("hair_back")],
        source=SOURCE_SEMANTIC,
        canvas=(1024, 1536),
        eyebrow_evidence={"absent": False, "sides": {}, "reason": ""},
    )

    assert not manifest.not_applicable
    assert "eyebrow:L" in manifest.missing_required
    assert "eyebrow:R" in manifest.missing_required


def test_static_draw_order_follows_the_standard_layer_order():
    """清单的静态绘制序必须来自权威表（ADR-003：层序的唯一权威）。"""
    names = ["mouth", "face", "hair_back", "hair_front", "neck", "clothes_top"]
    data = build_manifest(
        [_layer(name) for name in names],
        source=SOURCE_SEMANTIC, canvas=(1024, 1536)).to_dict()

    standard = [n for n in LayerComposer.STANDARD_LAYER_ORDER if n in names]
    assert data["draw_order"] == standard


def _layer(name: str, pixel_count: int = 1000) -> dict:
    return {
        "name": name,
        "path": f"/tmp/out/{name}.png",
        "pixel_count": pixel_count,
        "bbox": [0, 0, 10, 10],
        "provenance": "detector",
        "confidence": 0.95,
    }


RIG_COMPLETE = [
    "hair_back",
    "hair_bangs",
    "face",
    "eyebrow_L",
    "eyebrow_R",
    "eyes_L",
    "eyes_R",
    "mouth",
    "neck",
    "torso",
    "clothes_top",
    "arm_L",
    "arm_R",
    "hand_L",
    "hand_R",
    "leg_L",
    "leg_R",
]


def test_color_cluster_is_never_rig_ready() -> None:
    layers = [_layer("hair_back"), _layer("face"), _layer("mouth"), _layer("neck"), _layer("clothes_top")]
    manifest = build_manifest(layers, source=SOURCE_COLOR_CLUSTER, canvas=(1024, 1024))

    assert manifest.rig_ready is False
    assert any("颜色聚类" in w for w in manifest.warnings)


def test_semantic_complete_set_is_rig_ready() -> None:
    manifest = build_manifest(
        [_layer(name) for name in RIG_COMPLETE],
        source=SOURCE_SEMANTIC,
        canvas=(2048, 2560),
    )

    assert manifest.rig_ready is True
    assert manifest.preview_ready is True
    assert manifest.missing_required == []
    assert manifest.canvas == {"width": 2048, "height": 2560}


def test_derived_layers_can_export_without_claiming_quality_ready() -> None:
    layers = [_layer(name) for name in RIG_COMPLETE]
    for layer in layers:
        layer["provenance"] = "derived_geometry"
        layer["confidence"] = 0.42

    manifest = build_manifest(
        layers, source=SOURCE_SEMANTIC, canvas=(2048, 2560))
    data = manifest.to_dict()

    assert manifest.rig_ready is True
    assert data["quality_ready"] is False
    assert all(layer["status"] == "derived" for layer in data["layers"])
    assert all(layer["source_mask"] == layer["id"] for layer in data["layers"])
    assert all("parent_group" in layer for layer in data["layers"])


def test_confirmed_layers_pass_quality_readiness() -> None:
    layers = [_layer(name) for name in RIG_COMPLETE]
    for layer in layers:
        layer["provenance"] = "detector"
        layer["confidence"] = 0.95

    manifest = build_manifest(
        layers, source=SOURCE_SEMANTIC, canvas=(2048, 2560))

    assert manifest.rig_ready is True
    assert manifest.to_dict()["quality_ready"] is True


def test_missing_evidence_is_manual_required_not_detected() -> None:
    layers = [_layer(name) for name in RIG_COMPLETE]
    layers[-1].pop("provenance")
    layers[-1].pop("confidence")

    manifest = build_manifest(
        layers, source=SOURCE_SEMANTIC, canvas=(2048, 2560))
    data = manifest.to_dict()
    leg_right = next(layer for layer in data["layers"] if layer["id"] == "leg_R")

    assert leg_right["status"] == "manual_required"
    assert leg_right["provenance"] == "unknown"
    assert leg_right["source_mask"] == "leg_R"
    assert leg_right["parent_group"] == leg_right["group"]
    assert manifest.rig_ready is False
    assert manifest.quality_ready is False


def test_color_heuristic_is_exportable_but_never_quality_ready() -> None:
    layers = [_layer(name) for name in RIG_COMPLETE]
    for layer in layers:
        layer["provenance"] = "color_heuristic"
        layer["confidence"] = 0.95

    manifest = build_manifest(
        layers, source=SOURCE_SEMANTIC, canvas=(2048, 2560))

    assert manifest.rig_ready is True
    assert manifest.quality_ready is False


def test_manual_required_layer_blocks_rig_readiness() -> None:
    layers = [_layer(name) for name in RIG_COMPLETE]
    layers[-1]["status"] = "manual_required"

    manifest = build_manifest(
        layers, source=SOURCE_SEMANTIC, canvas=(2048, 2560))

    assert manifest.rig_ready is False
    assert "leg:R" in manifest.missing_required


def test_left_right_suffix_resolves_to_side() -> None:
    assert resolve_part("eyes_left") == ("eye_white", "eye_white", "L")
    assert resolve_part("eyes_right") == ("eye_white", "eye_white", "R")
    assert resolve_part("arm_L") == ("arm", "arm", "L")
    assert resolve_part("arm_R") == ("arm", "arm", "R")


def test_unsided_limbs_are_reported_as_missing_rig_parts() -> None:
    layers = [
        _layer("hair_back"),
        _layer("hair_front"),
        _layer("face"),
        _layer("eyes_left"),
        _layer("eyes_right"),
        _layer("mouth"),
        _layer("neck"),
        _layer("clothes_top"),
        _layer("arms"),
        _layer("hands"),
        _layer("legs"),
    ]
    manifest = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(1024, 1024))

    assert manifest.rig_ready is False
    assert "arm:L" in manifest.missing_required
    assert "arm:R" in manifest.missing_required
    assert any("手臂未区分左右" in w for w in manifest.warnings)


def test_manifest_roundtrip_and_validation(tmp_path: Path) -> None:
    manifest = build_manifest(
        [_layer(name) for name in RIG_COMPLETE],
        source=SOURCE_SEMANTIC,
        canvas=(1024, 1024),
    )
    path = write_manifest(str(tmp_path), manifest)
    assert Path(path).name == MANIFEST_FILENAME

    data = load_manifest(str(tmp_path))
    assert data["schema_version"] == SCHEMA_VERSION
    assert validate_manifest(data) == []

    broken = json.loads(json.dumps(data))
    broken["layers"][0]["group"] = "不存在"
    broken["layers"][1]["side"] = "X"
    errors = validate_manifest(broken)
    assert any("未知分组" in e for e in errors)
    assert any("side" in e for e in errors)


def test_draw_order_is_sorted_by_z() -> None:
    manifest = build_manifest(
        [_layer(name) for name in RIG_COMPLETE],
        source=SOURCE_SEMANTIC,
        canvas=(1024, 1024),
    )
    data = manifest.to_dict()
    z_by_id = {layer["id"]: layer["z"] for layer in data["layers"]}
    ordered_z = [z_by_id[layer_id] for layer_id in data["draw_order"]]
    assert ordered_z == sorted(ordered_z)


def test_empty_layers_are_not_preview_ready() -> None:
    manifest = build_manifest([], source=SOURCE_SEMANTIC, canvas=(512, 512))
    assert manifest.preview_ready is False
    assert manifest.rig_ready is False
    assert pytest.approx(len(manifest.missing_required)) == 16


def test_seethrough_vocabulary_resolves() -> None:
    assert resolve_part("topwear")[0] == "clothes_top"
    assert resolve_part("bottomwear")[0] == "clothes_bottom"
    assert resolve_part("legwear")[0] == "sock"
    assert resolve_part("footwear")[0] == "boot"
    assert resolve_part("eyewhite")[0] == "eye_white"
    assert resolve_part("irides")[0] == "eye_iris"
    assert resolve_part("handwear_L") == ("handwear", "handwear", "L")
    assert resolve_part("ears_R")[0] == "ear"


def test_depth_order_is_used_when_every_layer_has_depth() -> None:
    layers = [
        {"name": "face", "path": "/tmp/face.png", "depth": 0.4},
        {"name": "hair_back", "path": "/tmp/hair_back.png", "depth": 0.1},
        {"name": "hair_front", "path": "/tmp/hair_front.png", "depth": 0.9},
    ]
    data = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(10, 10)).to_dict()

    assert data["order_source"] == "depth"
    assert data["draw_order"] == ["hair_back", "face", "hair_front"]


def test_partial_depth_falls_back_to_static_order() -> None:
    layers = [
        {"name": "face", "path": "/tmp/face.png", "depth": 0.4},
        {"name": "mouth", "path": "/tmp/mouth.png"},
    ]
    data = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(10, 10)).to_dict()

    assert data["order_source"] == "static"
    assert any("缺少深度" in w for w in data["warnings"])


# ---------------------------------------------------------------------------
# is_hollow：导出闸门的判据
#
# 线画在"一个必需部件都没认出来"，不是 preview_ready=false —— 实测本机 14 个
# 真实分层目录里 10 个 preview_ready=false（生产默认的 semantic 路径只缺一个
# mouth 也算），拿后者当闸门会把用户已经做好的作品全部拦死。
# ---------------------------------------------------------------------------

def test_hollow_only_when_no_required_part_survives() -> None:
    assert is_hollow(list(REQUIRED_FOR_PREVIEW)) is True
    # 只要还剩一个，就是"不完美"而不是"空心"。
    assert is_hollow([p for p in REQUIRED_FOR_PREVIEW if p != "hair_back"]) is False


def test_hollow_ignores_ids_outside_the_required_vocabulary() -> None:
    """missing_preview 里混进表外 id 时不得被数成"全缺"。

    判据必须按必需部件逐个核对，用 len(missing) >= len(required) 会被
    清单里多出来的未知部件名骗到，把正常分层判成空心。
    """
    leftovers = [p for p in REQUIRED_FOR_PREVIEW if p != "mouth"]
    assert is_hollow(leftovers + ["背景", "shadow", "unknown_part"]) is False


# ---------------------------------------------------------------------------
# P0 左右一致：整段肢体 vs 上下分段
# ---------------------------------------------------------------------------

SEGMENTED_LIMBS = [
    "arm_upper_L", "arm_lower_L", "arm_upper_R", "arm_lower_R",
    "thigh_L", "calf_L", "thigh_R", "calf_R",
]


def test_segmented_limbs_satisfy_the_whole_limb_slots() -> None:
    """上下两段齐全 = 整条肢体齐全，不能反过来要求 arm/leg 整段仍在。"""
    names = [n for n in RIG_COMPLETE if n not in ("arm_L", "arm_R", "leg_L", "leg_R")]
    names += SEGMENTED_LIMBS

    manifest = build_manifest([_layer(n) for n in names],
                              source=SOURCE_SEMANTIC, canvas=(1024, 1024))

    assert manifest.rig_ready is True
    assert "arm:L" not in manifest.missing_required
    assert "leg:R" not in manifest.missing_required


def test_partial_limb_segment_keeps_the_limb_missing() -> None:
    """只拆出上段（缺小腿/小臂）时整条肢体仍算缺件，并要求人工确认。"""
    names = [n for n in RIG_COMPLETE if n not in ("arm_L", "arm_R")]
    names += ["arm_upper_L"]

    manifest = build_manifest([_layer(n) for n in names],
                              source=SOURCE_SEMANTIC, canvas=(1024, 1024))

    assert "arm:L" in manifest.missing_required
    assert manifest.rig_ready is False
    assert any("只拆出部分段" in w for w in manifest.warnings)


def test_missing_one_side_blocks_rig_ready() -> None:
    names = [n for n in RIG_COMPLETE if n != "hand_R"]

    manifest = build_manifest([_layer(n) for n in names],
                              source=SOURCE_SEMANTIC, canvas=(1024, 1024))

    assert manifest.rig_ready is False
    assert "hand:R" in manifest.missing_required


def test_sided_coverage_is_reported_and_outliers_warn() -> None:
    """逐侧覆盖是诊断字段：覆盖悬殊要给出警告，但不直接改判 ready。"""
    manifest = build_manifest(
        [_layer("arm_L", 950), _layer("arm_R", 50)],
        source=SOURCE_SEMANTIC, canvas=(1024, 1024))
    data = manifest.to_dict()

    assert data["sided_coverage"]["arm"] == {"L": 950, "R": 50}
    assert any("左右覆盖差异过大" in w for w in data["warnings"])


# ---------------------------------------------------------------------------
# 遮挡补全：可导出，但推断像素不能算进「高质量」
# ---------------------------------------------------------------------------

def test_completed_layers_are_traced_but_never_quality_ready() -> None:
    layers = [_layer(name) for name in RIG_COMPLETE]
    for layer in layers:
        layer["status"] = "completed"
        layer["provenance"] = "amodal_completion"
        layer["completed_pixels"] = 200

    manifest = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(1024, 1024))
    data = manifest.to_dict()

    assert manifest.rig_ready is True
    assert manifest.quality_ready is False
    assert all(entry["completed_pixels"] == 200 for entry in data["layers"])
    assert any("推断像素" in w for w in data["warnings"])


def test_clothing_inner_and_outer_stay_distinct() -> None:
    """内外层不能落到同一个语义槽：否则「内衬 / 外套」在绑定端无法区分。"""
    assert resolve_part("clothes_inner")[1] == "lining"
    assert resolve_part("clothes_outer")[1] == "outer_body"
