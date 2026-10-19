from __future__ import annotations

import numpy as np
import pytest

from core.segment_engine.layer_manifest import SOURCE_SEMANTIC, build_manifest
from core.segment_engine.part_split import (
    SIDED_SOURCE_PARTS,
    attach_depths,
    estimate_midline,
    is_sided_name,
    split_depth_parts,
    split_eye_parts,
    split_garment_details,
    split_hair_parts,
    split_limb_parts,
    split_mask_sides,
)


def _rect(h: int, w: int, x0: int, x1: int, y0: int, y1: int) -> np.ndarray:
    mask = np.zeros((h, w), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def test_midline_uses_face_bbox_center() -> None:
    masks = {"face": _rect(40, 100, 40, 61, 5, 25)}
    assert estimate_midline(masks, 100) == 50.0


def test_midline_falls_back_to_canvas_center() -> None:
    masks = {"hair_back": _rect(40, 100, 0, 100, 0, 40)}
    assert estimate_midline(masks, 100) == 49.5


def test_split_assigns_character_left_to_viewer_right() -> None:
    face = _rect(40, 100, 40, 61, 5, 25)
    arms = _rect(40, 100, 60, 71, 25, 35) | _rect(40, 100, 20, 31, 25, 35)

    masks, warnings = split_limb_parts({"face": face, "arms": arms}, (100, 40))

    assert "arms" not in masks
    assert set(masks) == {"face", "arm_L", "arm_R"}
    assert not warnings

    assert masks["arm_L"][25:35, 60:71].all()
    assert not masks["arm_L"][25:35, 20:31].any()
    assert masks["arm_R"][25:35, 20:31].all()
    assert not masks["arm_R"][25:35, 60:71].any()


def test_single_side_is_reported_not_duplicated() -> None:
    face = _rect(40, 100, 40, 61, 5, 25)
    arms = _rect(40, 100, 60, 71, 25, 35)

    masks, warnings = split_limb_parts({"face": face, "arms": arms}, (100, 40))

    assert "arm_L" in masks
    assert "arm_R" not in masks
    assert any("单侧" in w for w in warnings)


def test_tiny_fragment_is_dropped() -> None:
    mask = _rect(40, 100, 60, 71, 0, 40)
    mask[0, 49] = True

    sides = split_mask_sides(mask, midline=50.0)

    assert set(sides) == {"L"}


def test_split_names_satisfy_manifest_rig_requirements() -> None:
    layers = [{"name": f"arm_{side}", "path": f"/tmp/arm_{side}.png"} for side in ("L", "R")]
    manifest = build_manifest(layers, source=SOURCE_SEMANTIC, canvas=(100, 100))

    assert "arm:L" not in manifest.missing_required
    assert "arm:R" not in manifest.missing_required


def test_sided_name_helpers() -> None:
    assert is_sided_name("arm_L")
    assert is_sided_name("hand_right")
    assert not is_sided_name("hands")
    assert SIDED_SOURCE_PARTS["arms"] == "arm"


def test_eyebrows_are_split_into_independent_sides() -> None:
    face = _rect(40, 100, 40, 61, 5, 25)
    eyebrows = _rect(40, 100, 58, 66, 8, 11) | _rect(40, 100, 35, 43, 8, 11)

    masks, warnings = split_limb_parts({"face": face, "eyebrows": eyebrows}, (100, 40))

    assert set(masks) == {"face", "eyebrow_L", "eyebrow_R"}
    assert not warnings


def test_cuff_is_cut_from_arm_bottom_without_overlap() -> None:
    face = _rect(100, 100, 40, 61, 5, 25)
    arm = _rect(100, 100, 60, 80, 20, 60)

    masks, derived = split_garment_details({"face": face, "arm_L": arm}, (100, 100))

    assert "cuff_L" in masks
    assert "arm_L" in masks
    assert derived == ["cuff_L"]
    assert not (masks["cuff_L"] & masks["arm_L"]).any()

    cuff_rows = np.where(masks["cuff_L"].any(axis=1))[0]
    arm_rows = np.where(masks["arm_L"].any(axis=1))[0]
    assert cuff_rows.min() > arm_rows.min()


def test_skirt_hem_is_split_into_left_and_right() -> None:
    face = _rect(100, 100, 40, 61, 5, 25)
    skirt = _rect(100, 100, 20, 80, 40, 100)

    masks, derived = split_garment_details({"face": face, "clothes_bottom": skirt}, (100, 100))

    assert "skirt_hem_L" in masks
    assert "skirt_hem_R" in masks
    assert "waistband" in masks
    assert {"skirt_hem_L", "skirt_hem_R", "waistband"} <= set(derived)
    assert not (masks["skirt_hem_L"] & masks["skirt_hem_R"]).any()


def test_collar_is_cut_from_top_band() -> None:
    clothes_top = _rect(100, 100, 30, 70, 20, 90)

    masks, derived = split_garment_details({"clothes_top": clothes_top}, (100, 100))

    assert "collar" in masks
    assert derived == ["collar"]
    collar_rows = np.where(masks["collar"].any(axis=1))[0]
    body_rows = np.where(masks["clothes_top"].any(axis=1))[0]
    assert collar_rows.max() < body_rows.max()


def test_boot_cuff_is_cut_from_boot_top() -> None:
    boot = _rect(100, 100, 20, 40, 60, 100)

    masks, derived = split_garment_details({"boot_L": boot}, (100, 100))

    assert "boot_cuff_L" in masks
    assert "boot_L" in masks
    assert derived == ["boot_cuff_L"]
    cuff_rows = np.where(masks["boot_cuff_L"].any(axis=1))[0]
    boot_rows = np.where(masks["boot_L"].any(axis=1))[0]
    assert cuff_rows.min() < boot_rows.max()


def test_band_ratios_match_tuning_constants() -> None:
    arm = _rect(200, 100, 60, 80, 20, 120)
    masks, _ = split_garment_details({"arm_L": arm}, (100, 200))
    cuff_rows = int(np.where(masks["cuff_L"].any(axis=1))[0].size)
    assert cuff_rows == 10

    skirt = _rect(200, 100, 20, 80, 20, 120)
    masks, _ = split_garment_details({"clothes_bottom": skirt}, (100, 200))
    hem_rows = int(np.where((masks["skirt_hem_L"] | masks["skirt_hem_R"]).any(axis=1))[0].size)
    assert hem_rows == 8


def test_eye_mask_splits_into_sclera_and_iris() -> None:
    h, w = 40, 100
    eye = _rect(h, w, 60, 90, 10, 20)
    img = np.full((h, w, 3), 30, dtype=np.uint8)
    img[10:20, 60:90] = (250, 250, 250)
    img[12:18, 68:82] = (40, 90, 160)

    masks, warnings = split_eye_parts({"eyes_left": eye}, img)

    assert "eyewhite_left" in masks
    assert "irides_left" in masks
    assert not (masks["eyewhite_left"] & masks["irides_left"]).any()
    assert masks["irides_left"].sum() < masks["eyewhite_left"].sum()
    assert not warnings


def test_uniform_eye_is_not_split() -> None:
    h, w = 40, 100
    eye = _rect(h, w, 60, 90, 10, 20)
    img = np.full((h, w, 3), 240, dtype=np.uint8)

    masks, warnings = split_eye_parts({"eyes_left": eye}, img)

    assert "eyes_left" in masks
    assert "irides_left" not in masks
    assert any("明暗差不足" in w for w in warnings)


def test_duplicate_hair_masks_are_split_by_face_box() -> None:
    hair = _rect(100, 100, 10, 90, 10, 90)
    face = _rect(100, 100, 35, 65, 30, 70)

    masks, warnings = split_hair_parts(
        {"hair_front": hair.copy(), "hair_back": hair.copy(), "face": face}
    )

    assert not np.array_equal(masks["hair_front"], masks["hair_back"])
    assert not (masks["hair_front"] & masks["hair_back"]).any()
    assert int(masks["hair_front"].sum()) == int((hair & face).sum())
    assert any("拆分" in w for w in warnings)


def test_hair_splits_by_top_band_without_face() -> None:
    hair = _rect(100, 100, 10, 90, 10, 90)

    masks, _ = split_hair_parts({"hair_back": hair})

    assert masks["hair_front"].any()
    assert masks["hair_back"].any()
    assert not (masks["hair_front"] & masks["hair_back"]).any()


def test_limb_crossing_midline_is_not_cut() -> None:
    # 左臂跨过中线，右臂在远处：按连通域/空隙拆分应保持每条手臂完整。
    arms = _rect(60, 100, 40, 57, 10, 30) | _rect(60, 100, 64, 77, 10, 30)

    sides = split_mask_sides(arms, midline=50.0)

    assert set(sides) == {"L", "R"}
    assert sides["R"][10:30, 40:57].all()
    assert sides["L"][10:30, 64:77].all()
    assert not sides["R"][10:30, 64:77].any()


def test_depth_split_uses_depth_map() -> None:
    h, w = 60, 100
    skirt = _rect(h, w, 20, 80, 20, 60)
    depth = np.zeros((h, w), dtype=np.float32)
    depth[20:40, :] = 0.2
    depth[40:60, :] = 0.8

    masks, warnings = split_depth_parts({"skirt": skirt}, depth)

    assert "skirt" not in masks
    assert "skirt_front" in masks
    assert "skirt_back" in masks
    assert not (masks["skirt_front"] & masks["skirt_back"]).any()
    assert masks["skirt_front"][40:60, 20:80].all()
    assert masks["skirt_back"][20:40, 20:80].all()
    assert any("按深度拆为" in w for w in warnings)


def test_uniform_depth_does_not_split() -> None:
    skirt = _rect(60, 100, 20, 80, 20, 60)
    depth = np.full((60, 100), 0.5, dtype=np.float32)

    masks, warnings = split_depth_parts({"skirt": skirt}, depth)

    assert "skirt" in masks
    assert "skirt_front" not in masks
    assert any("深度差不足" in w for w in warnings)


def test_attach_depths_sets_mean_depth_per_layer() -> None:
    near = _rect(40, 100, 60, 80, 10, 30)
    far = _rect(40, 100, 20, 40, 10, 30)
    depth = np.zeros((40, 100), dtype=np.float32)
    depth[:, 60:80] = 0.9
    depth[:, 20:40] = 0.1
    layers = {"near": {"name": "near"}, "far": {"name": "far"}}

    attach_depths(layers, {"near": near, "far": far}, depth)

    assert layers["near"]["depth"] == pytest.approx(0.9, abs=1e-3)
    assert layers["far"]["depth"] == pytest.approx(0.1, abs=1e-3)


def test_seethrough_sided_names_split_into_left_and_right() -> None:
    face = _rect(40, 100, 40, 61, 5, 25)
    handwear = _rect(40, 100, 60, 71, 25, 35) | _rect(40, 100, 20, 31, 25, 35)

    masks, _ = split_limb_parts({"face": face, "handwear": handwear}, (100, 40))

    assert {"handwear_L", "handwear_R"} <= set(masks)


def test_derived_layers_are_flagged_in_manifest() -> None:
    manifest = build_manifest(
        [{"name": "cuff_L", "path": "/tmp/cuff_L.png"}],
        source=SOURCE_SEMANTIC,
        canvas=(100, 100),
    )
    assert manifest.layers[0].derived is True
    assert manifest.to_dict()["layers"][0]["derived"] is True
