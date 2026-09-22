"""Offline tests for the SAM2 + GroundingDINO segmentation backend.

These cover only what can be verified without a GPU and without network
access: prompt-table completeness, the lazy-load contract, the
``ModelUnavailable`` failure surface (no HSV degradation), and the
missing-part / coverage accounting.

The real GPU end-to-end run lives in ``tools/verify_sam2_gd.py`` and is
deliberately NOT part of this file.
"""

import numpy as np
import pytest
from PIL import Image

import core.segment_engine.sam2_gd as mod
from core.segment_engine.sam2_gd import (
    EYES_QUERY,
    FACE_GATED_PARTS,
    METHOD_SAM1,
    METHOD_SAM2,
    Detection,
    ModelUnavailable,
    Sam2GroundingDinoSegmenter,
)
from core.segment_engine.semantic import SemanticSegmenter


def make_image(w=120, h=160):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    arr[:, :, 3] = 255
    return Image.fromarray(arr, "RGBA")


def det(x0, y0, x1, y1, score=0.7, label="thing"):
    return Detection(box=(float(x0), float(y0), float(x1), float(y1)), score=score, label=label)


def offline_segmenter(monkeypatch, detections=None, masks=None, sam_kind="sam2"):
    """A segmenter whose model-facing seams are replaced by canned answers."""
    seg = Sam2GroundingDinoSegmenter(device="cpu")
    monkeypatch.setattr(Sam2GroundingDinoSegmenter, "load", lambda self: self)
    seg._loaded = True
    seg._sam_kind = sam_kind
    seg._sam_id = "fake"

    canned_dets = detections if detections is not None else {}
    canned_masks = masks if masks is not None else {}

    def fake_detect(image):
        return {q: list(d) for q, d in canned_dets.items()}

    def fake_sam_masks(image, boxes):
        out, scores = [], []
        for b in boxes:
            key = tuple(round(float(v)) for v in b)
            out.append(canned_masks.get(key, np.zeros((image.size[1], image.size[0]), dtype=bool)))
            scores.append(0.5)
        return out, scores

    monkeypatch.setattr(seg, "detect", fake_detect)
    monkeypatch.setattr(seg, "sam_masks", fake_sam_masks)
    return seg


class TestBoxFiltering:
    def _seg_with_canned_raw(self, monkeypatch, raw_by_phrase):
        seg = Sam2GroundingDinoSegmenter(device="cpu")
        monkeypatch.setattr(Sam2GroundingDinoSegmenter, "load", lambda self: self)

        def fake_query(rgb, phrase):
            return list(raw_by_phrase.get(phrase, []))

        monkeypatch.setattr(seg, "_detect_query", fake_query)
        return seg

    def test_whole_frame_boxes_are_dropped_and_recorded(self, monkeypatch):
        phrase = Sam2GroundingDinoSegmenter.PART_PROMPTS["face"]
        seg = self._seg_with_canned_raw(monkeypatch, {
            phrase: [det(0, 0, 120, 160, 0.40), det(20, 20, 60, 60, 0.55)],
        })
        out = seg.detect(make_image())
        assert [d.box for d in out["face"]] == [(20.0, 20.0, 60.0, 60.0)]
        assert seg.dropped_boxes and "face" in seg.dropped_boxes[0]

    def test_tiny_boxes_are_dropped(self, monkeypatch):
        phrase = Sam2GroundingDinoSegmenter.PART_PROMPTS["nose"]
        seg = self._seg_with_canned_raw(monkeypatch, {
            phrase: [det(60, 60, 61, 61, 0.9)],   # 1 px, below min_box_area_ratio
        })
        out = seg.detect(make_image())
        assert out["nose"] == []
        assert seg.dropped_boxes == []   # tiny drops are silent, frame drops are not

    def test_nms_keeps_the_highest_score(self, monkeypatch):
        phrase = Sam2GroundingDinoSegmenter.PART_PROMPTS["mouth"]
        seg = self._seg_with_canned_raw(monkeypatch, {
            phrase: [det(10, 10, 40, 40, 0.30), det(11, 11, 41, 41, 0.80)],
        })
        out = seg.detect(make_image())
        assert len(out["mouth"]) == 1
        assert out["mouth"][0].score == pytest.approx(0.80)


class TestFaceGate:
    """The gate that keeps head parts from being grounded on body regions."""

    def test_off_face_mouth_box_is_dropped_not_masked(self, monkeypatch):
        # Replicates the measured failure: on docs/assets/demo_input.png
        # "mouth . lips" scored highest on the character's knees.
        face = [det(257, 101, 342, 196, 0.72)]
        dets = {
            "face": face,
            "mouth": [det(255, 630, 285, 660, 0.28), det(315, 629, 344, 660, 0.28)],
        }
        seg = offline_segmenter(monkeypatch, dets)
        assigned, missing, notes = seg._assign_parts(dets, (600, 900))
        assert "mouth" not in assigned
        assert "mouth" in missing
        assert any("face envelope" in n for n in notes)
        assert any("mouth" in b for b in seg.dropped_boxes)

    def test_on_face_boxes_survive_the_gate(self, monkeypatch):
        face = [det(257, 101, 342, 196, 0.72)]
        dets = {
            "face": face,
            "eyebrows": [det(305, 105, 341, 138, 0.35)],
            "nose": [det(288, 145, 313, 179, 0.30)],
            EYES_QUERY: [det(259, 134, 286, 154, 0.67), det(313, 134, 340, 153, 0.67)],
        }
        seg = offline_segmenter(monkeypatch, dets)
        assigned, missing, notes = seg._assign_parts(dets, (600, 900))
        assert {"face", "eyebrows", "nose", "eyes_left", "eyes_right"} <= set(assigned)
        assert not any("envelope" in n for n in notes)

    def test_non_head_parts_are_never_gated(self, monkeypatch):
        # A necktie sits well below the face and must survive.
        dets = {
            "face": [det(257, 101, 342, 196, 0.72)],
            "accessories": [det(286, 218, 315, 356, 0.82)],
            "legs": [det(304, 507, 377, 883, 0.59)],
        }
        seg = offline_segmenter(monkeypatch, dets)
        assigned, _missing, notes = seg._assign_parts(dets, (600, 900))
        assert "accessories" in assigned and "legs" in assigned
        assert not any("envelope" in n for n in notes)

    def test_gate_is_skipped_when_no_face_was_found(self, monkeypatch):
        dets = {"mouth": [det(255, 630, 285, 660, 0.28)]}
        seg = offline_segmenter(monkeypatch, dets)
        assigned, _missing, notes = seg._assign_parts(dets, (600, 900))
        assert "mouth" in assigned, "without a face reference nothing may be gated"
        assert any("cannot be gated" in n for n in notes)

    def test_face_gated_parts_are_all_head_parts(self):
        assert FACE_GATED_PARTS <= set(SemanticSegmenter.STANDARD_PARTS)
        assert "face" not in FACE_GATED_PARTS
        assert not (FACE_GATED_PARTS & {"legs", "hands", "arms", "clothes_top"})


# --------------------------------------------------------------- prompt table


class TestPromptTable:
    def test_covers_every_standard_part(self):
        assert set(Sam2GroundingDinoSegmenter.covered_parts()) == set(SemanticSegmenter.STANDARD_PARTS)

    def test_no_extra_or_duplicate_parts(self):
        covered = Sam2GroundingDinoSegmenter.covered_parts()
        assert len(covered) == len(set(covered)) == len(SemanticSegmenter.STANDARD_PARTS)

    def test_every_query_is_a_part_or_the_eyes_query(self):
        parts = set(SemanticSegmenter.STANDARD_PARTS)
        for query in Sam2GroundingDinoSegmenter.PART_PROMPTS:
            assert query in parts or query == EYES_QUERY

    def test_prompts_are_non_empty_lowercase_phrases(self):
        for query, phrase in Sam2GroundingDinoSegmenter.PART_PROMPTS.items():
            assert isinstance(phrase, str) and phrase.strip(), query
            assert phrase == phrase.lower(), query

    def test_every_prompt_has_two_synonym_forms(self):
        # MEASURED on grounding-dino-tiny: a single-token prompt returns zero
        # boxes even when the object is obvious ("face" -> 0 detections vs
        # "face . character face" -> 0.72). One synonym per prompt therefore
        # silently starves whole parts, so the table is pinned to >= 2.
        for query, phrase in Sam2GroundingDinoSegmenter.PART_PROMPTS.items():
            synonyms = [s.strip() for s in phrase.split(".") if s.strip()]
            assert len(synonyms) >= 2, f"{query}: {phrase!r}"
            assert len(set(synonyms)) == len(synonyms), f"{query}: duplicate synonym"

    def test_frame_size_filter_is_configured_sensibly(self):
        seg = Sam2GroundingDinoSegmenter(device="cpu")
        assert 0.0 < seg.min_box_area_ratio < seg.max_box_area_ratio < 1.0

    def test_eyes_are_split_not_grounding_grounded(self):
        # GroundingDINO is unreliable at viewer-left/right wording, so both eye
        # parts must come from a single spatially-split query.
        assert EYES_QUERY in Sam2GroundingDinoSegmenter.PART_PROMPTS
        assert "eyes_left" not in Sam2GroundingDinoSegmenter.PART_PROMPTS
        assert "eyes_right" not in Sam2GroundingDinoSegmenter.PART_PROMPTS


# ------------------------------------------------------------- lazy / no load


class TestLazyConstruction:
    def test_construction_loads_nothing(self):
        seg = Sam2GroundingDinoSegmenter(device="cpu")
        assert seg.is_loaded is False
        assert seg._dino is None and seg._dino_proc is None
        assert seg._sam is None and seg._sam_proc is None

    def test_hf_endpoint_defaulted_before_transformers_import(self):
        # huggingface.co is unreachable on the target machines.
        import os
        assert os.environ["HF_ENDPOINT"] == "https://hf-mirror.com"

    def test_method_property_defaults_to_sam2_label(self):
        assert Sam2GroundingDinoSegmenter(device="cpu").method == METHOD_SAM2

    def test_nest_boxes_emits_pure_float_lists(self):
        # The transformers processor only recurses on ``list``: tuples or numpy
        # rows are miscounted as 2 nesting levels and the whole call fails.
        # This exact shape bug broke the first real GPU run, so pin it here.
        nested = Sam2GroundingDinoSegmenter._nest_boxes(
            [(1.0, 2.0, 3.0, 4.0), np.array([5, 6, 7, 8])]
        )
        assert nested == [[[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]]
        assert type(nested) is list and type(nested[0]) is list
        assert type(nested[0][0]) is list
        assert all(type(v) is float for v in nested[0][0])


# ------------------------------------------------------------ ModelUnavailable


class TestModelUnavailable:
    @pytest.mark.skipif(not mod.HAS_TORCH, reason="needs torch installed to isolate the transformers-missing path")
    def test_missing_transformers_raises(self, monkeypatch):
        monkeypatch.setattr(mod, "HAS_TRANSFORMERS", False)
        seg = Sam2GroundingDinoSegmenter(device="cpu")
        with pytest.raises(ModelUnavailable, match="transformers"):
            seg.load()

    def test_missing_torch_raises(self, monkeypatch):
        monkeypatch.setattr(mod, "HAS_TORCH", False)
        seg = Sam2GroundingDinoSegmenter(device="cpu")
        with pytest.raises(ModelUnavailable, match="PyTorch"):
            seg.load()

    @pytest.mark.skipif(not mod.HAS_TORCH, reason="needs torch installed to inspect torch.cuda")
    def test_explicit_cuda_without_cuda_raises(self, monkeypatch):
        monkeypatch.setattr(mod, "HAS_TORCH", True)
        monkeypatch.setattr(mod.torch.cuda, "is_available", lambda: False)
        seg = Sam2GroundingDinoSegmenter(device="cuda")
        with pytest.raises(ModelUnavailable, match="cuda"):
            seg.load()

    def test_uncached_weights_raise_instead_of_degrading(self):
        # local_files_only=True guarantees no network access in this test.
        seg = Sam2GroundingDinoSegmenter(
            device="cpu",
            detector_id="lives2d-does-not-exist/grounding-dino-tiny",
            sam2_ids=("lives2d-does-not-exist/sam2-hiera-tiny",),
            sam1_ids=("lives2d-does-not-exist/sam-vit-base",),
            local_files_only=True,
        )
        with pytest.raises(ModelUnavailable):
            seg.load()

    def test_layer_propagates_instead_of_running_hsv_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mod, "HAS_TRANSFORMERS", False)
        called = []
        monkeypatch.setattr(
            SemanticSegmenter,
            "_fallback_color_segment",
            lambda self, image: called.append(1) or {},
        )
        seg = SemanticSegmenter(device="cpu", model_type="sam2_gd")
        with pytest.raises(ModelUnavailable):
            seg.layer(make_image(), output_dir=str(tmp_path / "layers"))
        assert called == [], "sam2_gd must never reach the HSV fallback"

    def test_segment_propagates_instead_of_running_hsv_fallback(self, monkeypatch):
        monkeypatch.setattr(mod, "HAS_TRANSFORMERS", False)
        monkeypatch.setattr(
            SemanticSegmenter,
            "_fallback_color_segment",
            lambda self, image: pytest.fail("HSV fallback must not run for sam2_gd"),
        )
        seg = SemanticSegmenter(device="cpu", model_type="sam2_gd")
        with pytest.raises(ModelUnavailable):
            seg.segment(make_image())


# --------------------------------------------------------- part accounting


class TestPartAssignment:
    def test_eyes_split_around_face_centre(self, monkeypatch):
        dets = {
            "face": [det(40, 20, 80, 60)],
            EYES_QUERY: [det(45, 30, 55, 40, 0.8), det(65, 30, 75, 40, 0.8)],
        }
        seg = offline_segmenter(monkeypatch, dets)
        assigned, missing, notes = seg._assign_parts(dets, (120, 160))
        assert "eyes_left" in assigned and "eyes_right" in assigned
        assert assigned["eyes_left"][0].box[0] < assigned["eyes_right"][0].box[0]
        assert notes == []

    def test_single_eye_does_not_invent_the_other(self, monkeypatch):
        dets = {"face": [det(40, 20, 80, 60)], EYES_QUERY: [det(45, 30, 55, 40)]}
        seg = offline_segmenter(monkeypatch, dets)
        assigned, missing, notes = seg._assign_parts(dets, (120, 160))
        assert "eyes_left" in assigned and "eyes_right" not in assigned
        assert "eyes_right" in missing
        assert any("only one side" in n for n in notes)

    def test_missing_parts_lists_every_undetected_part(self, monkeypatch):
        seg = offline_segmenter(monkeypatch, {"face": [det(40, 20, 80, 60)]})
        assigned, missing, _ = seg._assign_parts({"face": [det(40, 20, 80, 60)]}, (120, 160))
        assert set(assigned) == {"face"}
        assert set(missing) == set(SemanticSegmenter.STANDARD_PARTS) - {"face"}


class TestSegmentDetailedAccounting:
    def test_detected_part_reports_coverage_and_confidence(self, monkeypatch):
        h, w = 160, 120
        mask = np.zeros((h, w), dtype=bool)
        mask[20:60, 40:80] = True          # 1600 px of 19200
        seg = offline_segmenter(
            monkeypatch,
            {"face": [det(40, 20, 80, 60, score=0.64)]},
            {(40, 20, 80, 60): mask},
        )
        masks, report = seg.segment_detailed(make_image())
        assert list(masks) == ["face"]
        ev = report["parts_report"]["face"]
        assert ev["detected"] is True
        assert ev["confidence"] == pytest.approx(0.64)
        assert ev["sam_iou"] == pytest.approx(0.5)
        assert ev["coverage"] == pytest.approx(1600 / 19200, abs=1e-6)
        assert ev["pixel_count"] == 1600
        assert report["method"] == METHOD_SAM2

    def test_undetected_parts_are_missing_not_all_zero(self, monkeypatch):
        mask = np.zeros((160, 120), dtype=bool)
        mask[10:30, 10:30] = True
        seg = offline_segmenter(
            monkeypatch,
            {"face": [det(40, 20, 80, 60)], "mouth": [det(50, 50, 70, 58)]},
            {(40, 20, 80, 60): mask},   # mouth returns an all-zero mask
        )
        masks, report = seg.segment_detailed(make_image())
        assert "mouth" not in masks, "an empty mask must never be handed downstream"
        assert "mouth" in report["missing_parts"]
        assert report["parts_report"]["mouth"]["detected"] is False
        assert report["parts_report"]["mouth"]["coverage"] == 0.0
        assert any("empty mask" in n for n in report["parts_report"]["mouth"]["notes"])
        assert "face" in report["detected_parts"]

    def test_nothing_detected_yields_no_masks_and_all_parts_missing(self, monkeypatch):
        seg = offline_segmenter(monkeypatch, {})
        masks, report = seg.segment_detailed(make_image())
        assert masks == {}
        assert report["missing_parts"] == sorted(SemanticSegmenter.STANDARD_PARTS)
        assert report["detected_parts"] == []

    def test_multiple_boxes_union_into_one_part_mask(self, monkeypatch):
        h, w = 160, 120
        a = np.zeros((h, w), dtype=bool); a[0:10, 0:10] = True
        b = np.zeros((h, w), dtype=bool); b[50:60, 50:60] = True
        seg = offline_segmenter(
            monkeypatch,
            {"arms": [det(0, 0, 10, 10, 0.5), det(50, 50, 60, 60, 0.9)]},
            {(0, 0, 10, 10): a, (50, 50, 60, 60): b},
        )
        masks, report = seg.segment_detailed(make_image())
        assert int(masks["arms"].sum()) == 200
        assert report["parts_report"]["arms"]["box_count"] == 2
        assert report["parts_report"]["arms"]["confidence"] == pytest.approx(0.9)


class TestMethodHonesty:
    def test_sam1_substitute_is_labelled_differently(self, monkeypatch):
        mask = np.zeros((160, 120), dtype=bool)
        mask[:20, :20] = True
        seg = offline_segmenter(
            monkeypatch, {"face": [det(0, 0, 20, 20)]}, {(0, 0, 20, 20): mask}, sam_kind="sam1"
        )
        _, report = seg.segment_detailed(make_image())
        assert report["method"] == METHOD_SAM1
        assert report["method"] != METHOD_SAM2
        assert report["promptable_segmenter"] == "sam1"

    @pytest.mark.skipif(not mod.HAS_TRANSFORMERS, reason="needs transformers installed to reach the promptable stage")
    def test_sam1_fallback_can_be_disabled(self, monkeypatch):
        # No weights, no network: only the promptable-segmenter stage is driven.
        seg = Sam2GroundingDinoSegmenter(
            device="cpu",
            sam2_ids=("lives2d-does-not-exist/sam2-hiera-tiny",),
            sam1_ids=("lives2d-does-not-exist/sam-vit-base",),
            local_files_only=True,
            allow_sam1_fallback=False,
        )
        seg._dino = object()  # pretend the detector stage already succeeded
        with pytest.raises(ModelUnavailable, match="allow_sam1_fallback"):
            seg._load_promptable_segmenter()


class TestLayerContract:
    def test_layer_returns_semantic_shape(self, monkeypatch, tmp_path):
        mask = np.zeros((160, 120), dtype=bool)
        mask[20:80, 30:90] = True
        seg = offline_segmenter(
            monkeypatch, {"face": [det(30, 20, 90, 80, score=0.6)]}, {(30, 20, 90, 80): mask}
        )
        result = seg.layer(make_image(), output_dir=str(tmp_path))
        # Keys consumed by core/workflow.py
        for key in ("success", "method", "layers", "output_dir", "preview_path",
                    "composite_preview", "layer_count", "k_clusters", "segmentation_mask"):
            assert key in result, key
        assert result["success"] is True
        assert result["method"] == METHOD_SAM2
        assert result["layer_count"] == len(result["layers"]) == 1
        layer = result["layers"][0]
        for key in ("index", "name", "part_name", "path", "pixel_count", "bbox", "size"):
            assert key in layer, key
        # Honest extras
        assert result["missing_parts"] == sorted(set(SemanticSegmenter.STANDARD_PARTS) - {"face"})
        assert result["parts_report"]["face"]["coverage"] > 0

    def test_layer_without_any_detection_reports_failure(self, monkeypatch, tmp_path):
        seg = offline_segmenter(monkeypatch, {})
        result = seg.layer(make_image(), output_dir=str(tmp_path))
        assert result["success"] is False
        assert result["layers"] == []
        assert result["layer_count"] == 0
        assert result["method"] == METHOD_SAM2
        assert result["missing_parts"] == sorted(SemanticSegmenter.STANDARD_PARTS)


class TestSemanticWiring:
    def test_model_type_passes_through_selector(self):
        seg = SemanticSegmenter(device="cpu", model_type="sam2_gd")
        assert seg.model_type == "sam2_gd"

    def test_semantic_delegates_layer_to_sam2_gd(self, monkeypatch):
        seen = {}

        class Sentinel:
            def __init__(self, device=None):
                seen["device"] = device

            def layer(self, image, output_dir=None, label_layers=True):
                seen["args"] = (output_dir, label_layers)
                return {"success": True, "method": METHOD_SAM2, "layers": []}

            def segment(self, image):
                seen["segmented"] = True
                return {}

        monkeypatch.setattr(mod, "Sam2GroundingDinoSegmenter", Sentinel)
        out = SemanticSegmenter(device="cpu", model_type="sam2_gd").layer(
            make_image(), output_dir="X", label_layers=False
        )
        assert out["method"] == METHOD_SAM2
        assert seen["device"] == "cpu"
        assert seen["args"] == ("X", False)

        SemanticSegmenter(device="cpu", model_type="sam2_gd").segment(make_image())
        assert seen["segmented"] is True
