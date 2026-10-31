from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _layer(name: str, pixel_count: int = 1000) -> dict:
    return {
        "name": name,
        "path": f"/tmp/out/{name}.png",
        "pixel_count": pixel_count,
        "bbox": [0, 0, 10, 10],
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

