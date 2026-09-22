#!/usr/bin/env python3
"""
Live2D Master Agent - SAM2 + GroundingDINO segmentation backend

Opens-vocabulary part separation for anime character images:

1. **GroundingDINO** (``IDEA-Research/grounding-dino-tiny``) turns a natural
   language prompt per Live2D part into bounding boxes + detection scores.
2. **SAM2** (``facebook/sam2-hiera-*``) turns those boxes into pixel-accurate
   masks via the real transformers image-prompt path
   (:meth:`Sam2Model.get_image_embeddings` + :meth:`Sam2Model.forward` with
   ``input_boxes`` — verified against transformers 5.17.0).
3. The result is returned in exactly the same dict shape as
   :meth:`core.segment_engine.semantic.SemanticSegmenter.layer`, so
   :class:`core.workflow.WorkflowEngine` can consume it unchanged.

Hard rule — no fake success
---------------------------
This module **never** degrades to an HSV / colour heuristic and never reports
a successful layering it did not actually compute:

* torch / CUDA / transformers / weights unavailable  -> :class:`ModelUnavailable`
  is raised, loudly, from :meth:`load` and from every entry point.
* a part with no GroundingDINO detection is reported in ``missing_parts`` and
  gets **no** mask (never an all-zero mask that downstream code accepts).
* every part carries ``confidence`` (detector score), ``sam_iou`` (SAM2's own
  quality estimate) and ``coverage`` (non-zero pixel fraction) so a caller can
  tell a real mask from an empty one.
* if SAM2 cannot be loaded at all, the only permitted substitute is SAM v1,
  and then ``method`` becomes ``"sam1_groundingdino"``, never
  ``"sam2_groundingdino"``.

Network note
------------
``huggingface.co`` is unreachable from the target machines while
``https://hf-mirror.com`` is, so ``HF_ENDPOINT`` is defaulted **before**
``transformers`` / ``huggingface_hub`` are imported. Nothing is downloaded at
import time or at construction time: weights load on :meth:`load` (or on first
use).
"""

from __future__ import annotations

import os

# Must precede any transformers/huggingface_hub import anywhere in the process.
# ``setdefault`` so an operator-supplied endpoint always wins.
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import time  # noqa: E402
from collections import OrderedDict  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from core.logger import get_logger  # noqa: E402
from core.segment_engine.semantic import SemanticSegmenter  # noqa: E402

log = get_logger("segment.sam2_gd")

try:
    import torch  # type: ignore
    HAS_TORCH = True
except ImportError:  # pragma: no cover
    HAS_TORCH = False

try:
    import transformers  # type: ignore
    HAS_TRANSFORMERS = True
except ImportError:  # pragma: no cover
    HAS_TRANSFORMERS = False


class ModelUnavailable(RuntimeError):
    """A required model dependency (torch / CUDA / weights) is unavailable.

    Raised instead of silently degrading to a colour heuristic. Callers that
    want a fallback must opt into it explicitly.
    """


# --------------------------------------------------------------------- prompts

#: GroundingDINO text prompt per detection query. Queries are *not* 1:1 with
#: parts: ``"eyes"`` is detected once and then split into ``eyes_left`` /
#: ``eyes_right`` by box centroid, because open-vocabulary models are unreliable
#: at viewer-left vs viewer-right grounding (see :meth:`_assign_parts`).
#:
#: MEASURED CONSTRAINT: every prompt needs **at least two synonym forms**
#: separated by ``" . "``. A single-token prompt returns zero boxes on
#: ``grounding-dino-tiny`` even when the object is plainly present — verified on
#: ``docs/assets/demo_input.png``, where ``"face"`` -> 0 detections but
#: ``"face . character face"`` -> 0.72. Hence the doubled synonyms below
#: (``"eye . eyes"``, ``"hand . hands"``, ``"nose . nostrils"`` ...).
PART_PROMPTS: Dict[str, str] = {
    "hair_back": "long hair . back hair . hair",
    "hair_front": "bangs . fringe . front hair",
    "face": "face . character face",
    "eyebrows": "eyebrow . eyebrows",
    "eyes": "eye . eyes",
    "nose": "nose . nostrils",
    "mouth": "mouth . lips",
    "neck": "neck . throat",
    "clothes_top": "shirt . blouse . top . jacket",
    "clothes_bottom": "skirt . pants . shorts . trousers",
    "arms": "arm . sleeve",
    "hands": "hand . hands",
    "legs": "leg . thigh . calf",
    # "hair ornament" and "glasses" are deliberately absent: measured on
    # docs/assets/demo_input.png they matched the *whole hair mass* (0.23 of the
    # frame) and the *eye band* respectively, so the accessories layer became a
    # hair/eye copy. This phrasing returns only the necktie.
    "accessories": "ribbon . necktie . hat . earrings . headwear",
}

#: Parts that must sit on or immediately around the detected face. A box for
#: these that misses the face envelope is a detector false positive, not a
#: real part: measured on the test image, ``"mouth . lips"`` returned its two
#: highest-scoring boxes at the character's *knees* (y 629-660 of a face that
#: ends at y 196). Gating on the detector's own face box (no colour heuristics
#: involved) turns that into an honest "missing part" instead of a wrong mask.
FACE_GATED_PARTS = frozenset({
    "eyebrows", "eyes_left", "eyes_right", "nose", "mouth",
})

#: Queries whose boxes are split into two parts by horizontal position.
EYES_QUERY = "eyes"

#: Honest backend identifiers. ``method`` is what ``core/workflow.py`` reads.
METHOD_SAM2 = "sam2_groundingdino"
METHOD_SAM1 = "sam1_groundingdino"

DEFAULT_DETECTOR_ID = "IDEA-Research/grounding-dino-tiny"
#: Small -> tiny: the 6 GB VRAM budget of the target box rules out the
#: ``sam2-hiera-base-plus`` and larger variants.
DEFAULT_SAM2_IDS: Tuple[str, ...] = ("facebook/sam2-hiera-small", "facebook/sam2-hiera-tiny")
#: Last-resort substitute, only reachable when SAM2 itself cannot load. The
#: ``method`` string changes accordingly.
DEFAULT_SAM1_IDS: Tuple[str, ...] = ("facebook/sam-vit-base",)


@dataclass
class Detection:
    """One GroundingDINO box for one query."""

    box: Tuple[float, float, float, float]  # x0, y0, x1, y1 in source pixels
    score: float
    label: str = ""


@dataclass
class PartEvidence:
    """Honest, per-part accounting of what the backend actually produced."""

    part: str
    detected: bool = False
    box_count: int = 0
    confidence: float = 0.0      # max GroundingDINO score over the used boxes
    sam_iou: float = 0.0         # SAM2's own IoU prediction for the best box
    coverage: float = 0.0        # fraction of image pixels set in the mask
    pixel_count: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "part": self.part,
            "detected": self.detected,
            "box_count": self.box_count,
            "confidence": round(float(self.confidence), 4),
            "sam_iou": round(float(self.sam_iou), 4),
            "coverage": round(float(self.coverage), 6),
            "pixel_count": int(self.pixel_count),
            "notes": list(self.notes),
        }


class Sam2GroundingDinoSegmenter:
    """GroundingDINO (text -> boxes) + SAM2 (boxes -> masks) part segmenter.

    Args:
        device: ``"auto"`` (prefers CUDA), ``"cpu"`` or ``"cuda"``. An explicit
            ``"cuda"`` request that cannot be honoured raises
            :class:`ModelUnavailable`; ``"auto"`` warns and continues on CPU.
        detector_id: GroundingDINO repo id.
        sam2_ids: SAM2 repo ids tried in order.
        sam1_ids: SAM v1 repo ids used only when no SAM2 checkpoint loads.
        box_threshold / text_threshold: GroundingDINO cut-offs.
        min_box_area_ratio: boxes smaller than this fraction of the image are
            dropped as detector noise.
        max_box_area_ratio: boxes larger than this fraction are dropped.
            GroundingDINO reliably emits one whole-character "catch-all" box per
            prompt (measured: an ``eyebrow`` box spanning 70% of the test image
            at score 0.26) which would otherwise flood every part with a
            near-full-frame mask.
        max_boxes_per_query / max_total_boxes: hard caps on prompt count.
        nms_iou: greedy IoU suppression threshold within one query.
        local_files_only: never touch the network; fail with
            :class:`ModelUnavailable` if the weights are not already cached.
        allow_sam1_fallback: when False, a SAM2 load failure raises instead of
            substituting SAM v1.
    """

    STANDARD_PARTS: List[str] = list(SemanticSegmenter.STANDARD_PARTS)
    PART_PROMPTS: Dict[str, str] = dict(PART_PROMPTS)

    def __init__(
        self,
        device: str = "auto",
        detector_id: str = DEFAULT_DETECTOR_ID,
        sam2_ids: Sequence[str] = DEFAULT_SAM2_IDS,
        sam1_ids: Sequence[str] = DEFAULT_SAM1_IDS,
        box_threshold: float = 0.26,
        text_threshold: float = 0.26,
        min_box_area_ratio: float = 0.00008,
        max_box_area_ratio: float = 0.35,
        face_gate_margin: float = 0.6,
        max_boxes_per_query: int = 6,
        max_total_boxes: int = 48,
        nms_iou: float = 0.55,
        local_files_only: bool = False,
        allow_sam1_fallback: bool = True,
    ) -> None:
        self.requested_device = device
        self.device = self._resolve_device(device)
        self.detector_id = detector_id
        self.sam2_ids = tuple(sam2_ids)
        self.sam1_ids = tuple(sam1_ids)
        self.box_threshold = float(box_threshold)
        self.text_threshold = float(text_threshold)
        self.min_box_area_ratio = float(min_box_area_ratio)
        self.max_box_area_ratio = float(max_box_area_ratio)
        self.face_gate_margin = float(face_gate_margin)
        self.max_boxes_per_query = int(max_boxes_per_query)
        self.max_total_boxes = int(max_total_boxes)
        self.nms_iou = float(nms_iou)
        self.local_files_only = bool(local_files_only)
        self.allow_sam1_fallback = bool(allow_sam1_fallback)

        # Nothing below is populated until load() runs — construction is free.
        self._dino = None
        self._dino_proc = None
        self._sam = None
        self._sam_proc = None
        self._sam_kind: Optional[str] = None      # "sam2" | "sam1"
        self._sam_id: Optional[str] = None
        self._loaded = False
        self.last_report: Dict[str, object] = {}
        #: Boxes discarded by the frame-size filter, kept for honest reporting.
        self.dropped_boxes: List[str] = []

    # ------------------------------------------------------------- availability

    @classmethod
    def covered_parts(cls) -> List[str]:
        """Every Live2D part the prompt table can, in principle, produce."""
        parts: List[str] = []
        for query in cls.PART_PROMPTS:
            if query == EYES_QUERY:
                parts += ["eyes_left", "eyes_right"]
            else:
                parts.append(query)
        return parts

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def method(self) -> str:
        """Honest backend identifier; valid only once :meth:`load` succeeded."""
        if self._sam_kind == "sam1":
            return METHOD_SAM1
        return METHOD_SAM2

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device == "auto":
            if HAS_TORCH and torch.cuda.is_available():  # type: ignore[name-defined]
                return "cuda"
            return "cpu"
        return device

    # -------------------------------------------------------------------- load

    def load(self) -> "Sam2GroundingDinoSegmenter":
        """Load detector + promptable segmenter. Idempotent. Never downloads
        at construction time and never falls back to a colour heuristic.

        Raises:
            ModelUnavailable: torch / CUDA / transformers / weights missing.
        """
        if self._loaded:
            return self

        if not HAS_TORCH:
            raise ModelUnavailable(
                "PyTorch is not installed in this interpreter; the SAM2 + "
                "GroundingDINO backend cannot run. Install torch (CUDA build) "
                "or select another segmentation backend."
            )
        if self.requested_device == "cuda" and not torch.cuda.is_available():  # type: ignore[name-defined]
            raise ModelUnavailable(
                "device='cuda' was requested but torch.cuda.is_available() is "
                "False (no CUDA driver / no GPU). Refusing to degrade."
            )
        if not HAS_TRANSFORMERS:
            raise ModelUnavailable(
                "transformers is not installed; GroundingDINO and SAM2 need "
                "it (`pip install transformers`). Refusing to degrade."
            )
        if self.device == "cpu":
            log.warning(
                "SAM2+GroundingDINO running on CPU — no CUDA device found. "
                "Expect tens of seconds per image."
            )

        self._load_detector()
        self._load_promptable_segmenter()
        self._loaded = True
        log.success(
            f"Loaded {self.detector_id} + {self._sam_kind} ({self._sam_id}) "
            f"on {self.device}"
        )
        return self

    def _from_pretrained(self, cls_name: str, repo_id: str):
        """``from_pretrained`` with the error surface normalised to
        :class:`ModelUnavailable`."""
        try:
            model_cls = getattr(transformers, cls_name)
        except AttributeError as exc:  # pragma: no cover - version drift
            raise ModelUnavailable(
                f"transformers {getattr(transformers, '__version__', '?')} has "
                f"no {cls_name}; this build predates SAM2/GroundingDINO support."
            ) from exc
        try:
            obj = model_cls.from_pretrained(repo_id, local_files_only=self.local_files_only)
        except Exception as exc:
            raise ModelUnavailable(
                f"Could not load {repo_id} ({cls_name}) from "
                f"endpoint={os.environ.get('HF_ENDPOINT')}: {exc}"
            ) from exc
        return obj

    def _load_detector(self) -> None:
        if self._dino is not None:
            return
        log.info(f"Loading GroundingDINO detector {self.detector_id} -> {self.device}")
        proc = self._from_pretrained("GroundingDinoProcessor", self.detector_id)
        model = self._from_pretrained("GroundingDinoForObjectDetection", self.detector_id)
        model.to(self.device).eval()
        self._dino_proc = proc
        self._dino = model

    def _load_promptable_segmenter(self) -> None:
        if self._sam is not None:
            return
        errors: List[str] = []
        for repo_id in self.sam2_ids:
            try:
                proc = self._from_pretrained("Sam2Processor", repo_id)
                model = self._from_pretrained("Sam2Model", repo_id)
                model.to(self.device).eval()
                self._sam_proc, self._sam = proc, model
                self._sam_kind, self._sam_id = "sam2", repo_id
                return
            except ModelUnavailable as exc:
                errors.append(f"  {repo_id}: {exc}")
        log.warning(
            "No SAM2 checkpoint could be loaded:\n" + "\n".join(errors)
        )
        if not self.allow_sam1_fallback:
            raise ModelUnavailable("SAM2 unavailable and allow_sam1_fallback=False")
        for repo_id in self.sam1_ids:
            try:
                proc = self._from_pretrained("SamProcessor", repo_id)
                model = self._from_pretrained("SamModel", repo_id)
                model.to(self.device).eval()
                self._sam_proc, self._sam = proc, model
                self._sam_kind, self._sam_id = "sam1", repo_id
                log.warning(
                    f"SAM2 unavailable -> using SAM v1 ({repo_id}). Results are "
                    f"labelled method='{METHOD_SAM1}', NOT '{METHOD_SAM2}'."
                )
                return
            except ModelUnavailable as exc:
                errors.append(f"  {repo_id}: {exc}")
        raise ModelUnavailable(
            "No promptable segmenter (SAM2 or SAM v1) could be loaded. "
            "Weights are fetched via HF_ENDPOINT="
            f"{os.environ.get('HF_ENDPOINT')}:\n" + "\n".join(errors)
        )

    # --------------------------------------------------------------- detection

    def detect(self, image: Image.Image) -> Dict[str, List[Detection]]:
        """Run one GroundingDINO pass per query.

        Returns:
            Query name -> list of :class:`Detection` (NMS-pruned, area-filtered).
        """
        self.load()
        rgb = image.convert("RGB")
        w, h = rgb.size
        img_area = float(max(1, w * h))
        self.dropped_boxes = []
        out: Dict[str, List[Detection]] = {}
        for query, phrase in self.PART_PROMPTS.items():
            raw = self._detect_query(rgb, phrase)
            kept = []
            for d in raw:
                area = (d.box[2] - d.box[0]) * (d.box[3] - d.box[1])
                if area < self.min_box_area_ratio * img_area:
                    continue
                if area > self.max_box_area_ratio * img_area:
                    self.dropped_boxes.append(
                        f"{query}: whole-frame box at {d.score:.2f}"
                    )
                    continue
                kept.append(d)
            dets = self._nms(kept, self.nms_iou)[: self.max_boxes_per_query]
            out[query] = dets
            if dets:
                log.debug(
                    f"{query}: {len(out[query])} boxes "
                    f"(max score {max(d.score for d in out[query]):.2f})"
                )
        return out

    def _detect_query(self, rgb: Image.Image, phrase: str) -> List[Detection]:
        """Single open-vocabulary detection pass for one prompt."""
        try:
            inputs = self._dino_proc(images=rgb, text=phrase, return_tensors="pt").to(self.device)
            with torch.no_grad():
                outputs = self._dino(**inputs)
            results = self._dino_proc.post_process_grounded_object_detection(
                outputs,
                input_ids=inputs["input_ids"],
                threshold=self.box_threshold,
                text_threshold=self.text_threshold,
                target_sizes=[rgb.size[::-1]],  # (h, w)
            )
        except ModelUnavailable:
            raise
        except Exception as exc:
            raise ModelUnavailable(
                f"GroundingDINO inference failed for prompt {phrase!r}: {exc}"
            ) from exc

        res = results[0]
        boxes = res["boxes"]
        scores = res["scores"]
        labels = res.get("text_labels") or [""] * len(boxes)
        dets: List[Detection] = []
        for box, score, label in zip(boxes, scores, labels):
            x0, y0, x1, y1 = (float(v) for v in box.tolist())
            dets.append(Detection(
                box=(max(0.0, x0), max(0.0, y0), min(float(rgb.width), x1), min(float(rgb.height), y1)),
                score=float(score),
                label=str(label),
            ))
        return dets

    @staticmethod
    def _box_iou(a: Sequence[float], b: Sequence[float]) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        ix0, iy0 = max(ax0, bx0), max(ay0, by0)
        ix1, iy1 = min(ax1, bx1), min(ay1, by1)
        iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
        inter = iw * ih
        area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
        area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _nms(dets: List[Detection], iou_thresh: float) -> List[Detection]:
        """Greedy IoU suppression, highest score first."""
        kept: List[Detection] = []
        for d in sorted(dets, key=lambda x: -x.score):
            if all(Sam2GroundingDinoSegmenter._box_iou(d.box, k.box) <= iou_thresh for k in kept):
                kept.append(d)
        return kept

    # ------------------------------------------------------------ box -> part

    def _assign_parts(
        self, detections: Dict[str, List[Detection]], image_size: Tuple[int, int]
    ) -> Tuple[Dict[str, List[Detection]], List[str], List[str]]:
        """Map detection queries onto canonical Live2D part names.

        ``eyes`` is split by box centroid against the face centre (falling back
        to the image centre): viewer-left -> ``eyes_left``, matching the
        convention already used by
        :meth:`core.segment_engine.semantic.SemanticSegmenter._classify_masks_anime`.

        Returns:
            ``(part -> detections, missing_parts, notes)``. A part is *missing*
            when the detector produced nothing usable for it — it is never
            emitted as an all-zero mask.
        """
        w, h = image_size
        assigned: Dict[str, List[Detection]] = {}
        notes: List[str] = []

        face = detections.get("face") or []
        fcx = float(np.mean([(d.box[0] + d.box[2]) / 2.0 for d in face])) if face else w / 2.0
        if not face:
            notes.append(
                "no 'face' box: eye left/right split falls back to image centre "
                "and head-part false positives cannot be gated"
            )

        for query, dets in detections.items():
            if query != EYES_QUERY:
                if dets:
                    assigned.setdefault(query, []).extend(dets)
                continue
            if not dets:
                continue
            left = [d for d in dets if (d.box[0] + d.box[2]) / 2.0 < fcx]
            right = [d for d in dets if (d.box[0] + d.box[2]) / 2.0 >= fcx]
            if not left or not right:
                # Only one eye found: do not invent the other side.
                notes.append("eyes: only one side detected, the other is reported missing")
            if left:
                assigned.setdefault("eyes_left", []).extend(left)
            if right:
                assigned.setdefault("eyes_right", []).extend(right)

        if face:
            self._apply_face_gate(assigned, face, notes)

        missing = [p for p in self.STANDARD_PARTS if p not in assigned]
        return assigned, missing, notes

    def _face_envelope(self, face_dets: List[Detection]) -> Tuple[float, float, float, float]:
        """The union face box grown by :attr:`face_gate_margin` of its own size."""
        x0 = min(d.box[0] for d in face_dets)
        y0 = min(d.box[1] for d in face_dets)
        x1 = max(d.box[2] for d in face_dets)
        y1 = max(d.box[3] for d in face_dets)
        m = self.face_gate_margin
        return (x0 - m * (x1 - x0), y0 - m * (y1 - y0),
                x1 + m * (x1 - x0), y1 + m * (y1 - y0))

    def _apply_face_gate(
        self,
        assigned: Dict[str, List[Detection]],
        face_dets: List[Detection],
        notes: List[str],
    ) -> None:
        """Drop :data:`FACE_GATED_PARTS` boxes that miss the face envelope.

        Mutates ``assigned`` in place and records what it removed.
        """
        ex0, ey0, ex1, ey1 = self._face_envelope(face_dets)
        for part in list(assigned):
            if part not in FACE_GATED_PARTS:
                continue
            keep, dropped = [], []
            for d in assigned[part]:
                overlaps = not (d.box[2] < ex0 or d.box[0] > ex1
                                or d.box[3] < ey0 or d.box[1] > ey1)
                (keep if overlaps else dropped).append(d)
            if dropped:
                assigned[part] = keep
                for d in dropped:
                    self.dropped_boxes.append(
                        f"{part}: off-face box at {d.score:.2f} "
                        f"{[round(v) for v in d.box]}"
                    )
                notes.append(
                    f"{part}: {len(dropped)} box(es) outside the face envelope were "
                    f"discarded as false positives (part may be reported missing)"
                )
            if not keep:
                assigned.pop(part, None)

    # ------------------------------------------------------------- masks (SAM)

    def sam_masks(
        self, image: Image.Image, boxes: List[Sequence[float]]
    ) -> Tuple[List[np.ndarray], List[float]]:
        """Prompt the segmenter with boxes and return full-size boolean masks.

        ``boxes`` are ``[x0, y0, x1, y1]`` in source-image pixels; the
        processor rescales them into the model's 1024 space.
        """
        if not boxes:
            return [], []
        self.load()
        rgb = image.convert("RGB")
        try:
            if self._sam_kind == "sam2":
                masks, scores = self._sam2_masks(rgb, boxes)
            else:
                masks, scores = self._sam1_masks(rgb, boxes)
        except ModelUnavailable:
            raise
        except Exception as exc:
            raise ModelUnavailable(
                f"{self._sam_kind} box-prompted inference failed on "
                f"{self._sam_id}: {exc}"
            ) from exc
        if len(masks) != len(boxes):
            raise ModelUnavailable(
                f"{self._sam_kind} returned {len(masks)} masks for "
                f"{len(boxes)} boxes; refusing to guess the alignment."
            )
        return masks, scores

    @staticmethod
    def _nest_boxes(boxes: Sequence[Sequence[float]]) -> List[List[List[float]]]:
        """``[[x0,y0,x1,y1], ...]`` -> the ``[image, box, coord]`` **list**
        nesting the SAM processors demand.

        Tuples and numpy arrays are rejected by
        ``ProcessorMixin._validate_fully_nested`` (it only recurses on ``list``),
        so every level is materialised as a plain float list.
        """
        return [[[float(v) for v in b] for b in boxes]]

    def _sam2_masks(
        self, rgb: Image.Image, boxes: List[Sequence[float]]
    ) -> Tuple[List[np.ndarray], List[float]]:
        """The verified transformers-5.17 SAM2 image path.

        One vision-encoder pass is reused for every box prompt by feeding
        ``get_image_embeddings`` output back into ``forward``.
        """
        inputs = self._sam_proc(
            images=rgb, input_boxes=self._nest_boxes(boxes), return_tensors="pt"
        )
        inputs = {k: (v.to(self.device) if torch.is_tensor(v) else v) for k, v in inputs.items()}
        with torch.no_grad():
            embeddings = self._sam.get_image_embeddings(inputs["pixel_values"])
            outputs = self._sam(
                input_boxes=inputs["input_boxes"],
                image_embeddings=embeddings,
                multimask_output=False,
            )
        post = self._sam_proc.post_process_masks(
            outputs.pred_masks, inputs["original_sizes"], binarize=True
        )
        mask_tensor = post[0].cpu()                  # (N, 1, H, W) bool
        iou = outputs.iou_scores.detach().float().cpu().numpy().reshape(mask_tensor.shape[0], -1)
        masks = [np.asarray(m, dtype=bool) for m in mask_tensor.squeeze(1)]
        return masks, [float(v[0]) for v in iou]

    def _sam1_masks(
        self, rgb: Image.Image, boxes: List[Sequence[float]]
    ) -> Tuple[List[np.ndarray], List[float]]:
        """SAM v1 substitute. Only reached when no SAM2 checkpoint loads; the
        caller-visible ``method`` becomes :data:`METHOD_SAM1` so this can never
        be mistaken for SAM2.

        NOTE: not exercised on this machine (SAM2 loaded fine), so it is
        deliberately the less-trusted path.
        """
        inputs = self._sam_proc(
            images=rgb, input_boxes=self._nest_boxes(boxes), return_tensors="pt"
        )
        inputs = {k: (v.to(self.device) if torch.is_tensor(v) else v) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self._sam(**inputs)
        post = self._sam_proc.post_process_masks(
            outputs.pred_masks,
            inputs["original_sizes"],
            inputs["reshaped_input_sizes"],
            binarize=True,
        )
        mask_tensor = post[0].cpu()
        masks = [np.asarray(m, dtype=bool) for m in mask_tensor.squeeze(1)]
        return masks, [0.0] * len(masks)

    # ----------------------------------------------------------------- segment

    def segment_detailed(
        self, image: Image.Image
    ) -> Tuple[Dict[str, np.ndarray], Dict[str, object]]:
        """Full pipeline with per-part accounting.

        Returns:
            ``(masks, report)`` where ``masks`` maps part -> boolean ``(H, W)``
            mask (only parts that actually got a detection **and** a non-empty
            mask) and ``report`` carries ``parts_report`` / ``missing_parts`` /
            ``method``.
        """
        self.load()
        t0 = time.time()
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        h, w = image.size[1], image.size[0]

        detections = self.detect(image)
        assigned, missing, notes = self._assign_parts(detections, image.size)

        flat_boxes: List[Sequence[float]] = []
        owners: List[Tuple[str, float]] = []      # (part, detector score)
        capped = False
        for part, dets in assigned.items():
            for d in dets:
                if len(flat_boxes) >= self.max_total_boxes:
                    capped = True
                    break
                flat_boxes.append(d.box)
                owners.append((part, d.score))
            if capped:
                break
        if capped:
            notes.append(f"box cap ({self.max_total_boxes}) reached, later parts truncated")

        masks: Dict[str, np.ndarray] = {}
        confs: Dict[str, List[float]] = {}
        ious: Dict[str, List[float]] = {}
        if flat_boxes:
            sam_masks_list, sam_ious = self.sam_masks(image, list(flat_boxes))
            for (part, det_score), mask, iou in zip(owners, sam_masks_list, sam_ious):
                if mask.shape != (h, w):
                    mask = np.asarray(
                        Image.fromarray(mask.astype(np.uint8) * 255, "L").resize(
                            (w, h), Image.NEAREST
                        )
                    ) > 0
                if not mask.any():
                    continue
                masks[part] = mask if part not in masks else (masks[part] | mask)
                confs.setdefault(part, []).append(float(det_score))
                ious.setdefault(part, []).append(float(iou))

        # Per-part accounting over the *merged* mask.
        parts_report: Dict[str, Dict[str, object]] = {}
        for part in self.STANDARD_PARTS:
            dets = assigned.get(part, [])
            mask = masks.get(part)
            cov = float(mask.mean()) if mask is not None else 0.0
            ev = PartEvidence(
                part=part,
                detected=bool(dets) and mask is not None and cov > 0.0,
                box_count=len(dets),
                confidence=max((d.score for d in dets), default=0.0),
                sam_iou=max(ious.get(part, [0.0]), default=0.0),
                coverage=cov,
                pixel_count=int(mask.sum()) if mask is not None else 0,
            )
            if not ev.detected:
                if not dets:
                    ev.notes.append("no GroundingDINO box above threshold")
                else:
                    ev.notes.append("box detected but SAM produced an empty mask")
                    if part not in missing:
                        missing.append(part)
            parts_report[part] = ev.to_dict()

        for note in notes:
            log.warning(note)

        total_cov = float(np.mean([r["coverage"] for r in parts_report.values()])) if parts_report else 0.0
        report: Dict[str, object] = {
            "method": self.method,
            "detector": self.detector_id,
            "promptable_segmenter": self._sam_kind,
            "segmenter_id": self._sam_id,
            "device": self.device,
            "image_size": [w, h],
            "parts_report": parts_report,
            "missing_parts": sorted(set(missing)),
            "detected_parts": sorted(p for p, r in parts_report.items() if r["detected"]),
            "mean_coverage": round(total_cov, 6),
            "box_count": len(flat_boxes),
            "dropped_boxes": list(self.dropped_boxes),
            "notes": notes,
            "elapsed_s": round(time.time() - t0, 3),
        }
        self.last_report = report
        return masks, report

    def segment(self, image: Image.Image) -> Dict[str, np.ndarray]:
        """Drop-in equivalent of :meth:`SemanticSegmenter.segment`.

        Raises :class:`ModelUnavailable` rather than returning HSV masks.
        """
        masks, _ = self.segment_detailed(image)
        return masks

    # ------------------------------------------------------------------- layer

    def layer(
        self,
        image: Image.Image,
        output_dir: Optional[str] = None,
        label_layers: bool = True,
    ) -> Dict:
        """Segment + export, returning the :meth:`SemanticSegmenter.layer` shape.

        Extra keys beyond that shape (``parts_report``, ``missing_parts``,
        ``detected_parts``, ``detector``, ``device``, ``elapsed_s``) are purely
        additive: ``core/workflow.py`` reads only ``layers`` / ``method`` /
        ``output_dir`` / ``preview_path`` / ``composite_preview`` /
        ``layer_count``.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        out = Path(output_dir) if output_dir else Path.cwd() / "output" / f"layers_{int(time.time())}"
        out.mkdir(parents=True, exist_ok=True)

        # Raises ModelUnavailable — this path never degrades to HSV.
        masks, report = self.segment_detailed(image)
        method = str(report["method"])

        if not masks:
            log.error(
                f"{method}: GroundingDINO detected no usable part for this image; "
                f"all {len(self.STANDARD_PARTS)} parts are missing. Returning an "
                f"explicit failure (no colour-heuristic substitution)."
            )
            return {
                "success": False,
                "method": method,
                "layers": [],
                "output_dir": str(out),
                "preview_path": None,
                "composite_preview": None,
                "layer_count": 0,
                "k_clusters": 0,
                "segmentation_mask": None,
                "guide_path": None,
                **report,
            }

        try:
            from core.segment_engine.composer import LayerComposer
            composer = LayerComposer(device=self.device)
            composed = composer.compose(masks, image, str(out))
            ordered = composer.reorder_layers(composed)
            composition = "layer_composer"
        except Exception as exc:
            log.warning(f"LayerComposer failed ({exc}); burning masks directly")
            ordered = self._direct_mask_export(masks, image, out)
            composition = "direct_mask_export"

        exported_layers: List[Dict] = []
        for idx, (part_name, info) in enumerate(ordered.items()):
            exported_layers.append({
                "index": idx,
                "name": part_name,
                "part_name": part_name,
                "part_name_en": part_name,
                "path": info["path"],
                "size": image.size,
                "pixel_count": info.get("pixel_count", 0),
                "label": idx,
                "color": info.get("mean_color", (128, 128, 128)),
                "mean_color": info.get("mean_color", (128, 128, 128)),
                "bbox": info.get("bbox", (0, 0, 0, 0)),
                "amodal_completed": bool(info.get("completed", False)),
            })

        preview_path = out / "preview.png"
        image.save(preview_path)
        composite_preview_path = out / "composite_preview.png"
        SemanticSegmenter._write_composite_preview(exported_layers, image.size, composite_preview_path)

        guide_path = out / "layer_guide.json"
        try:
            from core.segment_engine.composer import LayerComposer as _LC
            _LC(device=self.device).generate_layer_json(dict(ordered), str(guide_path))
        except Exception as exc:  # pragma: no cover - guide is advisory
            log.warning(f"layer_guide.json not written: {exc}")
            guide_path = None  # type: ignore[assignment]

        if report["missing_parts"]:
            log.warning(
                f"{method}: {len(report['missing_parts'])} part(s) had no detection "
                f"and were NOT emitted: {', '.join(report['missing_parts'])}"
            )
        log.success(
            f"{method}: {len(exported_layers)}/{len(self.STANDARD_PARTS)} parts layered "
            f"on {self.device} in {report['elapsed_s']}s -> {out}"
        )

        return {
            "success": True,
            "method": method,
            "layers": exported_layers,
            "output_dir": str(out),
            "preview_path": str(preview_path),
            "composite_preview": str(composite_preview_path),
            "layer_count": len(exported_layers),
            "k_clusters": 0,
            "segmentation_mask": None,
            "guide_path": str(guide_path) if guide_path else None,
            "composition": composition,
            **report,
        }

    def _direct_mask_export(
        self,
        masks: Dict[str, np.ndarray],
        image: Image.Image,
        out: Path,
    ) -> "OrderedDict[str, Dict]":
        """Burn each boolean mask into a transparent PNG (no amodal completion).

        Same output shape as :meth:`LayerComposer.compose`; flagged downstream
        via ``composition='direct_mask_export'`` so the caller knows the
        amodal step did not run.
        """
        img_arr = np.array(image.convert("RGBA"))
        ordered_names = [n for n in self.STANDARD_PARTS if n in masks] + \
                        sorted(n for n in masks if n not in self.STANDARD_PARTS)
        result: "OrderedDict[str, Dict]" = OrderedDict()
        for name in ordered_names:
            mask = np.asarray(masks[name], dtype=bool)
            layer_arr = np.zeros_like(img_arr)
            layer_arr[mask] = img_arr[mask]
            path = out / f"{name}.png"
            Image.fromarray(layer_arr, "RGBA").save(path)
            pixels = layer_arr[..., 3] > 0
            ys, xs = np.where(pixels)
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else (0, 0, 0, 0)
            mean_color = tuple(int(c) for c in layer_arr[pixels][:, :3].mean(axis=0)) if pixels.any() else (0, 0, 0)
            result[name] = {
                "name": name,
                "path": str(path),
                "bbox": bbox,
                "size": (int(bbox[2] - bbox[0] + 1), int(bbox[3] - bbox[1] + 1)),
                "pixel_count": int(pixels.sum()),
                "mean_color": mean_color,
                "occluded_by": [],
                "completed": False,
            }
        return result


__all__ = [
    "Sam2GroundingDinoSegmenter",
    "ModelUnavailable",
    "PART_PROMPTS",
    "FACE_GATED_PARTS",
    "METHOD_SAM2",
    "METHOD_SAM1",
    "Detection",
    "PartEvidence",
]
