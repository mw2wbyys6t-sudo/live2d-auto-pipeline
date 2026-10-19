from __future__ import annotations

import numpy as np

from core.segment_engine.layer_manifest import SOURCE_SEMANTIC, build_manifest
from core.segment_engine.part_split import (
    SIDED_SOURCE_PARTS,
    estimate_midline,
    is_sided_name,
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
