#!/usr/bin/env python3
"""
Pygmalion Studio - SAM2 + GroundingDINO segmentation backend

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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    "arms": "arm . sleeve . bare arm . forearm",
    # 张开的手掌需要显式提到手指/掌心：实测同一张图 ``hand . hands`` 只召回
    # 持麦那只手，加上 ``open hand . palm . fingers`` 后才会框出另一侧张开的
    # 手掌（box 368,288-427,363）。缺了它，那只手会被并进上衣层，manifest
    # 只剩 ``arm:R`` 缺件，模型里也生不出手臂参数。
    "hands": "hand . hands . open hand . palm . fingers",
    "legs": "leg . thigh . calf",
    # 长袜/鞋：不单列时白色长袜会被并进腿的皮肤、鞋子并进腿的下端，
    # 模型就没有可单独驱动的袜子层。
    "legwear": "socks . thigh highs . stockings",
    "footwear": "shoes . boots . footwear",
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

#: Distinguishes "attribute absent" from "attribute set to None" when the
#: face-crop pass saves and restores the per-instance prompt table.
_UNSET = object()


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
        fill_missing_with_color: bool = True,
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
        # 检测器分不开的部位（上衣/下装这种只靠语义区分的）用颜色在 SAM
        # 剪影内部补出，避免整层缺失。
        self.fill_missing_with_color = bool(fill_missing_with_color)

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
            max_area_ratio = self.max_box_area_ratio
            if query in {"clothes_top", "clothes_bottom"}:
                max_area_ratio = max(max_area_ratio, 0.60)
            for d in raw:
                area = (d.box[2] - d.box[0]) * (d.box[3] - d.box[1])
                if area < self.min_box_area_ratio * img_area:
                    continue
                if area > max_area_ratio * img_area:
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
        out = self._suppress_catch_all(out, img_area)
        return out

    #: 五官层：允许只有几十像素（色阶启发式给出的鼻子只有 13px），并且
    #: 必须落在脸掩码内才采用。名字与 ``LayerComposer.FACE_FEATURE_PARTS``
    #: 同义，这里只留补全会用到的规范名。
    FACE_FEATURE_PARTS = frozenset({
        "eyes", "eyes_left", "eyes_right", "eyebrows", "nose", "mouth",
        "eyewhite_left", "eyewhite_right", "irides_left", "irides_right",
        "eyelash_left", "eyelash_right",
    })

    #: 面积占比超过这个值的框，一旦在两个以上不同查询里重复出现，就是
    #: "整个角色"级别的兜底框，不是某个部位。
    CATCH_ALL_AREA_RATIO = 0.20
    #: 判定"同一个框"的 IoU 门槛。
    CATCH_ALL_IOU = 0.80

    def _suppress_catch_all(
        self,
        detections: Dict[str, List[Detection]],
        img_area: float,
    ) -> Dict[str, List[Detection]]:
        """丢掉跨查询重复的"整个角色"兜底框。

        实测（2026-11-16，grounding-dino-tiny / 1024×1536 立绘）：
        ``clothes_top`` / ``clothes_bottom`` / ``arms`` / ``accessories`` 四个
        查询各返回了一个 (200,267)-(822,1059) 级别的框（面积占比 0.31），
        彼此 IoU > 0.95 —— SAM 于是给这四个部位各切出一份"整个角色"的掩码，
        上衣、下装、手臂、头饰全是同一块，等于没分。

        判据不是单看面积（真裙子确实能占到 0.2）：**同一个框要在两个以上
        不同查询里重复出现**才算兜底框。只属于一个查询的大框仍然保留 —— 那
        可能真的是整条裙子。
        """
        flat = [(q, d) for q, dets in detections.items() for d in dets]
        drop_ids = set()
        for query, det in flat:
            area = (det.box[2] - det.box[0]) * (det.box[3] - det.box[1])
            if area < self.CATCH_ALL_AREA_RATIO * img_area:
                continue
            twins = {
                other_query
                for other_query, other in flat
                if other_query != query
                and self._box_iou(det.box, other.box) >= self.CATCH_ALL_IOU
            }
            if len(twins) >= 2:
                drop_ids.add(id(det))
                self.dropped_boxes.append(
                    f"{query}: catch-all box shared with "
                    f"{', '.join(sorted(twins))} at {det.score:.2f}"
                )
        if not drop_ids:
            return detections
        cleaned: Dict[str, List[Detection]] = {}
        for query, dets in detections.items():
            kept = [d for d in dets if id(d) not in drop_ids]
            if kept:
                cleaned[query] = kept
        log.info(f"丢掉了 {len(drop_ids)} 个跨查询重复的兜底框")
        return cleaned

    #: 五官相对**脸掩码**的面积上限。超出说明这个掩码抓到了整块皮肤
    #: （实测 nose=2706px / face=5823px = 46%，鼻子不可能占半张脸）。
    FACE_PART_AREA_CEILING: Dict[str, float] = {
        "nose": 0.06,
        "mouth": 0.08,
        "eyebrows": 0.45,
        "eyes_left": 0.18,
        "eyes_right": 0.18,
    }

    def _enforce_face_area_priors(
        self, masks: Dict[str, np.ndarray]
    ) -> Tuple[Dict[str, np.ndarray], List[str]]:
        """按"五官不会比脸大"的先验剔掉越界掩码。

        SAM 只按框给掩码，框略大将直接吞掉半张脸；这时留着一个错误的鼻子层
        比留空更糟（它会盖在脸上）。被剔掉的部件会进入 ``missing_parts``，
        再由颜色补全接手。
        """
        face = masks.get("face")
        if face is None or not np.asarray(face).any():
            return masks, []
        face_px = float(np.asarray(face).sum())
        notes: List[str] = []
        for part, ceiling in self.FACE_PART_AREA_CEILING.items():
            mask = masks.get(part)
            if mask is None:
                continue
            px = float(np.asarray(mask).sum())
            if px <= ceiling * face_px:
                continue
            masks.pop(part, None)
            notes.append(
                f"{part}: mask {int(px)}px 超过脸面积的 {ceiling:.0%}"
                f"（{int(face_px)}px），判定为误抓整块皮肤并剔掉"
            )
            self.dropped_boxes.append(f"{part}: area prior violated ({int(px)}px)")
        return masks, notes

    def _fill_missing_with_color(
        self, masks: Dict[str, np.ndarray], image: Image.Image
    ) -> Tuple[Dict[str, np.ndarray], List[str]]:
        """在 SAM 给出的剪影内部，用颜色规则补出检测器分不开的部件。

        只补 ``masks`` 里没有的部件，并且从补出的像素里**扣除已经被 SAM
        占据的部分** —— 保证补出来的层不会盖到脸上、更不会和头发重叠。
        """
        from core.segment_engine.semantic import SemanticSegmenter

        arr = np.array(image.convert("RGBA"))
        # 结构性部位（五官/四肢）是可靠的，补出来的衣饰绝不能再盖上去。
        # 头发**不**参与这个排除集：实测 hair_back 的 SAM 掩码有 12.7 万像素、
        # 覆盖了整个上半身，一旦把它算成"已占用"，上衣/下装的颜色补全会因为
        # 像素全被头发占着而一个都补不出来。
        hard = np.zeros(arr.shape[:2], dtype=bool)
        hair = np.zeros(arr.shape[:2], dtype=bool)
        structural = {"face", "eyes_left", "eyes_right", "nose", "mouth",
                      "eyebrows", "hands", "arms", "legs", "neck", "accessories"}
        for part, mask in masks.items():
            m = np.asarray(mask, dtype=bool)
            if part in ("hair_back", "hair_front"):
                hair |= m
            elif part in structural:
                hard |= m
        if not (hard | hair).any():
            return masks, []

        # 前景必须用**真实剪影**：`_fallback_color_segment` 靠前景推断身体
        # 中轴、腰线、裙摆宽度，喂残缺形状会把腰线算到错误位置（实测把
        # 184684px 的裙子压成 22090px）。优先用 alpha；没有 alpha 时走 ISNet
        # 前景，最后才退回"已占用并集"。
        segmenter = SemanticSegmenter(device=self.device)
        fg = np.zeros(arr.shape[:2], dtype=bool)
        fg_is_real = False
        if arr.shape[2] == 4:
            alpha = arr[:, :, 3] > 10
            if alpha.any() and float(alpha.mean()) <= 0.98:
                fg, fg_is_real = alpha, True
        if not fg.any():
            try:
                raw = segmenter._segment_rembg(image)  # noqa: SLF001
            except Exception:  # pragma: no cover - defensive
                raw = []
            if raw:
                fg = np.asarray(raw[0]["segmentation"], dtype=bool)
                fg_is_real = bool(fg.any())
        if not fg.any():
            fg = hard | hair
        # 只有拿到真实剪影时才用它裁剪；退回"已占用并集"时它缺了下半身，
        # 拿它裁剪会把补出来的衣服整块削掉。
        clip = fg if fg_is_real else np.ones_like(fg)

        try:
            parts = segmenter._fallback_color_segment(image, fg)  # noqa: SLF001
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(f"颜色补全失败，跳过：{exc}")
            return masks, []

        min_px = max(32, int(0.0005 * hard.size))
        face = masks.get("face")
        face_mask = np.asarray(face, dtype=bool) if face is not None else None
        filled: List[str] = []
        for part in self.STANDARD_PARTS:
            if part in masks:
                continue
            candidate = parts.get(part)
            if candidate is None:
                continue
            if part in self.FACE_FEATURE_PARTS:
                # 五官必须落在脸内，且允许只有几十像素（色阶启发式给出的鼻子
                # 只有 13px）。用 ~hard 会连脸一起减掉，所以这里单独走一条路。
                if face_mask is None:
                    continue
                filled_mask = np.asarray(candidate, dtype=bool) & face_mask
                if int(filled_mask.sum()) < 8:
                    continue
                masks[part] = filled_mask
                filled.append(part)
                continue
            filled_mask = np.asarray(candidate, dtype=bool) & ~hard & clip
            if int(filled_mask.sum()) < min_px:
                continue
            # 争议像素：SAM 说这是头发、颜色说这是衣服。按颜色距离仲裁 ——
            # 谁的中位色更近就归谁。长发垂在裙子上时这一步决定裙子上沿是否
            # 被整块让给头发（那会让裙子缺口）。
            contested = filled_mask & hair
            if contested.any():
                hair_only = hair & ~filled_mask
                cloth_only = filled_mask & ~hair
                hair_ref = self._median_rgb(arr, hair_only)
                cloth_ref = self._median_rgb(arr, cloth_only)
                if hair_ref is not None and cloth_ref is not None:
                    dist_h = np.linalg.norm(
                        arr[:, :, :3].astype(np.float32) - hair_ref, axis=2)
                    dist_c = np.linalg.norm(
                        arr[:, :, :3].astype(np.float32) - cloth_ref, axis=2)
                    # 必须"明显更像衣服"才从头发里夺，避免把发丝吃进衣服。
                    freed = contested & (dist_c < dist_h * 0.9)
                else:
                    freed = np.zeros_like(contested)
                filled_mask = (filled_mask & ~contested) | freed
            if int(filled_mask.sum()) < min_px:
                continue
            for hair_part in ("hair_back", "hair_front"):
                if hair_part in masks:
                    masks[hair_part] = np.asarray(masks[hair_part], dtype=bool) & ~filled_mask
            masks[part] = filled_mask
            hard |= filled_mask
            filled.append(part)
        for hair_part in ("hair_back", "hair_front"):
            if hair_part in masks and not np.asarray(masks[hair_part]).any():
                masks.pop(hair_part)
        return masks, filled

    @staticmethod
    def _median_rgb(arr: np.ndarray, mask: np.ndarray):
        """掩码内 RGB 中位色；掩码太空时返回 None。"""
        pixels = arr[:, :, :3][np.asarray(mask, dtype=bool)]
        if pixels.size < 30:
            return None
        return np.median(pixels, axis=0).astype(np.float32)

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

    # ------------------------------------------- region re-detection passes

    #: ROI re-detections. A spec runs only when one of its ``missing`` parts
    #: came back empty **and** one of its ``anchors`` has a mask to localise
    #: that region.
    #:
    #: Why this exists and why thresholds cannot: GroundingDINO resizes
    #: ``shortest_edge`` to 800 and SAM2 to ``size={1024,1024}``, so on a
    #: 1333x2000 canvas the head enters the model at ~110x130 px and the mouth
    #: at ~15x8 px. No score cut recognises what the encoder never resolved;
    #: cropping spends the same resolution budget where the part actually is.
    #: Measured on docs/assets/demo_input.png the face pass recovered a mouth
    #: sitting 1.00 inside the face mask, where the whole-canvas pass found
    #: nothing or the character's knees.
    #:
    #: hair / upper / lower variants were tried and **removed**: on a controlled
    #: A-B (same 2000x2000 cutout, only the round count differing) they changed
    #: nothing -- parts 13, defects 23, explained 0.1295 in both arms -- while
    #: costing +25s of CPU per run. Re-add one only with a measured case where
    #: it recovers a part.
    ROI_SPECS: Tuple[Dict[str, Any], ...] = (
        {"name": "face", "missing": ("mouth", "nose", "eyes_left", "eyes_right"),
         "prompts": ("face", "eyebrows", "eyes", "nose", "mouth"),
         "anchors": ("face",), "grow": 1.2},
    )

    #: A recovered mask this far outside the character silhouette is a false
    #: positive, not a find: refuse it instead of trusting the detector.
    ROI_LEAK_MAX = 0.05

    def _roi_box(self, masks: Dict[str, np.ndarray], anchors: Tuple[str, ...],
                 grow: float, image_size: Tuple[int, int]
                 ) -> Optional[Tuple[int, int, int, int]]:
        """Union bbox of whichever anchor masks exist, grown by ``grow``."""
        acc: Optional[Tuple[int, int, int, int]] = None
        for part in anchors:
            m = masks.get(part)
            if m is None or not m.any():
                continue
            ys, xs = np.nonzero(m)
            box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
            acc = box if acc is None else (min(acc[0], box[0]), min(acc[1], box[1]),
                                           max(acc[2], box[2]), max(acc[3], box[3]))
        if acc is None:
            return None
        x0, y0, x1, y1 = acc
        gw, gh = x1 - x0, y1 - y0
        if gw < 8 or gh < 8:
            return None
        w, h = image_size
        return (max(0, int(x0 - gw * grow)), max(0, int(y0 - gh * grow)),
                min(w, int(x1 + gw * grow)), min(h, int(y1 + gh * grow)))

    def _run_roi(self, image: Image.Image, box: Tuple[int, int, int, int],
                 spec: Dict[str, Any], missing: Tuple[str, ...],
                 fg: Optional[np.ndarray]
                 ) -> Tuple[Dict[str, np.ndarray], List[str]]:
        """One ROI round: restrict the prompts, re-segment, paste back."""
        prompts = {k: v for k, v in dict(self.PART_PROMPTS).items()
                   if k in spec["prompts"]}
        saved_prompts = self.__dict__.get("PART_PROMPTS", _UNSET)
        saved_dropped = list(self.dropped_boxes)
        saved_report = getattr(self, "last_report", None)
        self.__dict__["PART_PROMPTS"] = prompts
        self._in_roi_pass = True
        try:
            crop_masks, _ = self.segment_detailed(image.crop(box))
        except ModelUnavailable as exc:
            log.warning(f"ROI {spec['name']} 二次检测失败，保留主检测结果: {exc}")
            crop_masks = {}
        finally:
            self._in_roi_pass = False
            self.dropped_boxes = saved_dropped
            self.last_report = saved_report
            if saved_prompts is _UNSET:
                self.__dict__.pop("PART_PROMPTS", None)
            else:
                self.__dict__["PART_PROMPTS"] = saved_prompts

        h, w = image.size[1], image.size[0]
        recovered: Dict[str, np.ndarray] = {}
        notes: List[str] = []
        for part, cm in crop_masks.items():
            if part not in missing or part in recovered:
                continue
            full = np.zeros((h, w), dtype=bool)
            ch = min(cm.shape[0], box[3] - box[1])
            cw = min(cm.shape[1], box[2] - box[0])
            full[box[1]:box[1] + ch, box[0]:box[0] + cw] = cm[:ch, :cw]
            if not full.any():
                continue
            if fg is not None:
                leak = int((full & ~fg).sum()) / int(full.sum())
                if leak > self.ROI_LEAK_MAX:
                    notes.append(
                        f"ROI {spec['name']} 检出的 {part} 有 {leak:.2f} 在角色"
                        f"轮廓外（上限 {self.ROI_LEAK_MAX}），拒收")
                    continue
            recovered[part] = full
            notes.append(f"ROI {spec['name']} 二次检测补回 {part}"
                         f"（crop={box}，{int(full.sum())} px）")
        return recovered, notes

    def _roi_redetect(
        self, image: Image.Image, masks: Dict[str, np.ndarray]
    ) -> Tuple[Dict[str, np.ndarray], List[str]]:
        """Fill missing parts region by region. Never overwrites a real mask."""
        if getattr(self, "_in_roi_pass", False):
            return masks, []

        from core.segment_engine.layer_quality import foreground_of

        merged = dict(masks)
        notes: List[str] = []
        fg = foreground_of(image)
        for spec in self.ROI_SPECS:
            missing = tuple(p for p in spec["missing"] if p not in merged)
            if not missing:
                continue
            box = self._roi_box(merged, spec["anchors"], spec["grow"], image.size)
            if box is None:
                notes.append(f"ROI {spec['name']} 缺 {'/'.join(missing)}，"
                             "但没有任何锚点掩码可定位，跳过")
                continue
            recovered, roi_notes = self._run_roi(image, box, spec, missing, fg)
            merged.update(recovered)
            notes.extend(roi_notes)
        # The notes ride along in the report; layer() surfaces each one as a
        # warning already, so this pass does not log them again.
        return merged, notes

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

        # Parts the whole-canvas pass could not resolve get one more chance at
        # region scale, before any accounting is derived from `masks`.
        masks, roi_notes = self._roi_redetect(image, masks)
        notes.extend(roi_notes)

        masks, prior_notes = self._enforce_face_area_priors(masks)
        notes.extend(prior_notes)

        # 检测器给不出"上衣/下装"这种只靠语义区分的部位（实测兜底框被丢掉后
        # 两者都为空）。缺的部件用**颜色 + 几何**在前景内补：SAM 已经给出准确
        # 剪影，颜色只负责在剪影内部画分界，比从头猜整张图可靠得多。
        color_filled: List[str] = []
        if self.fill_missing_with_color:
            masks, filled = self._fill_missing_with_color(masks, image)
            color_filled = list(filled)
            if filled:
                notes.append(
                    "color-split filled the parts the detector could not separate: "
                    + ", ".join(sorted(filled))
                )
                missing = [p for p in missing if p not in filled]

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
            # 颜色补全出来的部件必须可与检测/ROI 结果区分：它们的 provenance
            # 是 color_heuristic，不能计入 detector 证据。
            "color_filled": sorted(color_filled),
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
        depth_map: Optional[np.ndarray] = None,
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
            composed = composer.compose(masks, image, str(out), depth_map=depth_map)
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
                "rest_opacity": float(info.get("rest_opacity", 1.0)),
                "amodal_completed": bool(info.get("completed", False)),
                # composer 已给出的派生/合成来源；检测层由 _annotate_evidence 补齐。
                "provenance": info.get("provenance"),
                "confidence": info.get("confidence"),
                "status": info.get("status"),
                "source_mask": info.get("source_mask", part_name),
                "derived": bool(info.get("derived", False)),
                "depth": info.get("depth"),
            })
        self._annotate_evidence(
            exported_layers,
            report.get("parts_report") or {},
            set(masks),
            set(report.get("color_filled") or []),
        )

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

    @staticmethod
    def _annotate_evidence(
        exported_layers: List[Dict],
        parts_report: Dict[str, Dict[str, object]],
        mask_names: set,
        color_filled: set,
    ) -> None:
        """把每层的来源与置信度写进 layer dict（manifest 的权威证据）。

        - 检测器直接给出的部件：``detector`` + GroundingDINO 分数；
        - 颜色补全：``color_heuristic``，无置信度（不得冒充检测）；
        - ROI 二次检测：``roi_redetect``，只有 SAM IoU 可作证据；
        - composer 已标来源的拆分/合成层：保持 ``derived``，不继承检测分数。
        """
        for layer in exported_layers:
            name = str(layer.get("name") or "")
            if layer.get("provenance"):
                layer.setdefault("status", "derived")
                layer.setdefault("source_mask", name)
                continue
            evidence = parts_report.get(name) or {}
            if evidence.get("detected"):
                layer["provenance"] = "detector"
                layer["confidence"] = float(evidence.get("confidence") or 0.0)
                layer["status"] = "detected"
            elif name in color_filled:
                layer["provenance"] = "color_heuristic"
                layer["status"] = "derived"
            elif name in mask_names:
                iou = float(evidence.get("sam_iou") or 0.0)
                layer["provenance"] = "roi_redetect"
                layer["confidence"] = iou if iou > 0 else None
                layer["status"] = "derived"
            else:
                layer["provenance"] = "derived_geometry"
                layer["status"] = "derived"
            layer.setdefault("source_mask", name)

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
