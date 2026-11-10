#!/usr/bin/env python3
"""Pygmalion Studio - ``l2d_sam_iter2`` 部位解析后端。

See-through 官方发布的部位解析模型（``24yearsold/l2d_sam_iter2``，Apache-2.0，
SAM-HQ ViT-H 微调，647M 参数，19 类）。与 :mod:`core.segment_engine.sam2_gd`
的分工：

* ``sam2_gd``：GroundingDINO 开词检测 + SAM2 box prompt，能靠提示词分左右，
  但部件语义取决于提示词质量；
* ``l2d_sam_iter2``：整图直接预测 19 类部位概率，边界来自训练数据。实测同一
  张立绘上，上衣/下装掩码重叠从 93%（SAM2 大框）降到 0.23%。

代价是 CPU 单张约 310s（1536x1024），所以只作为**显式选择**的后端，不参与
``auto`` 选择。

本模块只负责「产出 19 类掩码 + 如实报告」。绘制顺序与遮挡补全由
:class:`core.segment_engine.composer.LayerComposer` 完成，19→52 层的映射由
:mod:`core.segment_engine.layers52` 完成。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger
from core.segment_engine.layers52 import BODY_PARTS_19_TO_52, VALID_BODY_PARTS_V2
from core.segment_engine.sam2_gd import ModelUnavailable, PartEvidence

log = get_logger("segment.l2d_sam")

try:
    import torch  # type: ignore
    HAS_TORCH = True
except ImportError:  # pragma: no cover
    HAS_TORCH = False


METHOD_L2D_SAM = "l2d_sam_iter2"
MODEL_ID = "24yearsold/l2d_sam_iter2"

#: 模型通道数，必须与 :data:`VALID_BODY_PARTS_V2` 的顺序严格一致。
CLASS_NUM = len(VALID_BODY_PARTS_V2)

_LIVE2D_CACHE = Path.home() / ".cache" / "live2d"
DEFAULT_CHECKPOINT = _LIVE2D_CACHE / "models" / "l2d_sam_iter2" / "checkpoint-18000.pt"


def find_seethrough_common() -> Optional[Path]:
    """定位 See-through 的 ``common`` 目录（内含 ``modules/semanticsam.py``）。

    依次尝试环境变量 ``LIVE2D_SEETHROUGH_COMMON``、缓存里的常见解压路径，
    最后在 ``~/.cache/live2d`` 下递归找 ``semanticsam.py``。
    """
    candidates: List[Path] = []
    env = os.environ.get("LIVE2D_SEETHROUGH_COMMON")
    if env:
        candidates.append(Path(env).expanduser())
    candidates += [
        _LIVE2D_CACHE / "see-through" / "see-through-main" / "common",
        _LIVE2D_CACHE / "see-through-main" / "common",
        _LIVE2D_CACHE / "see-through" / "common",
    ]
    for cand in candidates:
        if (cand / "modules" / "semanticsam.py").is_file():
            return cand
    if _LIVE2D_CACHE.is_dir():
        for path in _LIVE2D_CACHE.rglob("semanticsam.py"):
            if path.parent.name == "modules":
                return path.parent.parent
    return None


class L2dSamSegmenter:
    """19 类部位解析后端，接口与 :class:`Sam2GroundingDinoSegmenter` 对齐。"""

    #: 该后端能覆盖的全部部件名（= 模型的 19 个通道）。
    STANDARD_PARTS: List[str] = list(VALID_BODY_PARTS_V2)

    def __init__(
        self,
        device: str = "auto",
        checkpoint: Optional[str] = None,
        common_dir: Optional[str] = None,
        threshold: float = 0.5,
    ) -> None:
        """Initialize the backend without loading weights.

        Args:
            device: ``auto`` / ``cpu`` / ``cuda``。
            checkpoint: 权重路径，默认 ``~/.cache/live2d/models/l2d_sam_iter2/
                checkpoint-18000.pt``。
            common_dir: See-through 的 ``common`` 目录，默认自动查找。
            threshold: 概率阈值。官方 demo 用 ``logits > 0``（等价 0.5），
                该参数保留用于按需放宽/收紧。
        """
        self.device_requested = (device or "auto").lower()
        self.device = self._resolve_device(self.device_requested)
        self.checkpoint = (Path(checkpoint).expanduser() if checkpoint
                           else DEFAULT_CHECKPOINT)
        self.common_dir = (Path(common_dir).expanduser() if common_dir
                           else find_seethrough_common())
        self.threshold = float(threshold)
        self._model = None
        self._loaded = False
        self.last_report: Dict[str, object] = {}
        log.debug(
            f"L2dSamSegmenter ready (device={self.device}, checkpoint={self.checkpoint}, "
            f"common={self.common_dir})"
        )

    # ------------------------------------------------------------------ static

    @classmethod
    def covered_parts(cls) -> List[str]:
        """该后端能产出的部件名。"""
        return list(cls.STANDARD_PARTS)

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device == "auto":
            if HAS_TORCH:
                return "cuda" if torch.cuda.is_available() else "cpu"
            return "cpu"
        return device

    @property
    def method(self) -> str:
        return METHOD_L2D_SAM

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # -------------------------------------------------------------------- load

    def load(self) -> "L2dSamSegmenter":
        """加载模型。失败时抛 :class:`ModelUnavailable`，不静默降级。"""
        if self._loaded:
            return self
        if not HAS_TORCH:
            raise ModelUnavailable(
                "l2d_sam_iter2 需要 torch；当前环境未安装（pip install torch）"
            )
        if self.common_dir is None or not (self.common_dir / "modules" / "semanticsam.py").is_file():
            raise ModelUnavailable(
                "找不到 See-through 源码。请把 LIVE2D_SEETHROUGH_COMMON 指向 "
                "<see-through>/common（需含 modules/semanticsam.py），"
                f"当前解析结果: {self.common_dir}"
            )
        if not self.checkpoint.is_file():
            raise ModelUnavailable(
                f"缺少 l2d_sam_iter2 权重: {self.checkpoint}。"
                "可从 https://huggingface.co/24yearsold/l2d_sam_iter2 下载 "
                "checkpoint-18000.pt 到该路径。"
            )

        common = str(self.common_dir)
        if common not in sys.path:
            sys.path.insert(0, common)
        try:
            from modules.semanticsam import SemanticSam  # type: ignore
            from utils.torch_utils import init_model_from_pretrained  # type: ignore
        except Exception as exc:  # pragma: no cover - 依赖外部源码
            raise ModelUnavailable(
                f"导入 See-through 模块失败（{self.common_dir}）: {exc}"
            ) from exc

        t0 = time.time()
        try:
            model = init_model_from_pretrained(
                pretrained_model_name_or_path=str(self.checkpoint),
                module_cls=SemanticSam,
                download_from_hf=False,
                model_args={"class_num": CLASS_NUM},
                device=self.device,
            )
            model = model.to(self.device).eval()
        except Exception as exc:  # pragma: no cover - 权重/显存
            raise ModelUnavailable(
                f"加载 l2d_sam_iter2 权重失败（{self.checkpoint}）: {exc}"
            ) from exc

        self._model = model
        self._loaded = True
        log.success(
            f"{METHOD_L2D_SAM}: 模型加载完成 ({time.time() - t0:.1f}s, "
            f"device={self.device}, classes={CLASS_NUM})"
        )
        return self

    # --------------------------------------------------------------- inference

    def segment_detailed(
        self, image: Image.Image
    ) -> Tuple[Dict[str, np.ndarray], Dict[str, object]]:
        """推理并返回 ``(part -> bool mask, report)``。

        Raise:
            ModelUnavailable: torch / 源码 / 权重任一缺失。
        """
        self.load()
        if image.mode != "RGB":
            image = image.convert("RGB")
        width, height = image.size

        t0 = time.time()
        with torch.inference_mode():
            preds = self._model.inference(image)  # type: ignore[union-attr]
        if not preds:
            raise ModelUnavailable("l2d_sam_iter2 推理返回空结果")
        logits = preds[0]
        if hasattr(logits, "detach"):
            # 模型权重是 bfloat16，numpy 没有对应 dtype：必须显式转 float32，
            # 否则 np.asarray 会抛 "Got unsupported ScalarType BFloat16"。
            logits = logits.detach().to("cpu").float()
        arr = np.asarray(logits, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[None]
        if arr.shape[0] != CLASS_NUM:
            raise ModelUnavailable(
                f"模型输出 {arr.shape[0]} 个通道，与 {CLASS_NUM} 类不符"
            )

        masks: Dict[str, np.ndarray] = {}
        parts_report: Dict[str, Dict[str, object]] = {}
        notes: List[str] = []
        missing: List[str] = []
        covered = np.zeros((height, width), dtype=bool)

        # 官方 demo 用 ``logits > 0``（= 概率 0.5）；这里换算成阈值对应的 logit。
        logit_threshold = float(np.log(self.threshold / max(1e-6, 1.0 - self.threshold)))
        for index, part in enumerate(VALID_BODY_PARTS_V2):
            raw = arr[index]
            detected = raw > logit_threshold
            evidence = PartEvidence(part=part)
            evidence.detected = bool(detected.any())
            if evidence.detected:
                # 概率必须在**模型分辨率**上算，resize 之后 raw 与掩码尺寸不再对应。
                probs = 1.0 / (1.0 + np.exp(-raw[detected]))
                evidence.confidence = float(probs.mean()) if probs.size else 0.0
                binary = (detected if detected.shape == (height, width)
                          else self._resize_mask(detected, (width, height)))
                evidence.pixel_count = int(binary.sum())
                evidence.coverage = evidence.pixel_count / float(width * height)
                ys, xs = np.nonzero(binary)
                evidence.notes.append(
                    f"bbox=({int(xs.min())},{int(ys.min())},"
                    f"{int(xs.max())},{int(ys.max())})"
                )
                masks[part] = binary
                covered |= binary
            else:
                missing.append(part)
            parts_report[part] = evidence.to_dict()

        notes.append(
            "该后端不产出检测框：parts_report 里的 box_count / sam_iou 恒为 0，"
            "confidence 表示掩码内平均概率"
        )
        # 19 类掩码是各自 sigmoid 的，互不排斥，可能重叠；覆盖总和超过帧面积
        # 说明有部件互相抢像素，值得在报告里点出来。
        total_cov = float(sum(r["coverage"] for r in parts_report.values()))
        if total_cov > 1.25:
            notes.append(
                f"19 类掩码覆盖总和 {total_cov:.2f}× 画面，存在部件重叠（正常现象，"
                "重叠区由 composer 的绘制顺序决定谁可见）"
            )

        # 前景覆盖率：输入带 alpha 时用 alpha 当画布，否则用掩码并集做参照。
        rgba = image.convert("RGBA")
        alpha = np.array(rgba)[:, :, 3] > 0
        if alpha.any() and not alpha.all():
            outside = int((covered & ~alpha).sum())
            uncovered = int((alpha & ~covered).sum())
            notes.append(
                f"落前景外 {outside / float(alpha.sum()):.2%}，"
                f"前景未覆盖 {uncovered / float(alpha.sum()):.2%}"
            )

        report: Dict[str, object] = {
            "method": METHOD_L2D_SAM,
            "model_id": MODEL_ID,
            "checkpoint": str(self.checkpoint),
            "device": self.device,
            "image_size": [width, height],
            "threshold": self.threshold,
            "parts_report": parts_report,
            "missing_parts": missing,
            "detected_parts": [p for p in VALID_BODY_PARTS_V2 if p in masks],
            "mean_coverage": round(total_cov / float(CLASS_NUM), 6),
            "coverage_sum": round(total_cov, 4),
            "elapsed_s": round(time.time() - t0, 3),
            "mapping": self._mapping_summary(),
            "notes": notes,
        }
        self.last_report = report
        for note in notes:
            log.info(f"{METHOD_L2D_SAM}: {note}")
        return masks, report

    def segment(self, image: Image.Image) -> Dict[str, np.ndarray]:
        """Drop-in 版 :meth:`SemanticSegmenter.segment`，失败时抛异常而非降级。"""
        masks, _ = self.segment_detailed(image)
        return masks

    @staticmethod
    def _mapping_summary() -> Dict[str, Dict[str, object]]:
        """19 → 52 层的映射摘要，随报告输出便于核对落层依据。"""
        return {
            name: {
                "status": spec["status"],
                "primary": spec["primary"],
                "standard_ids": list(spec["standard_ids"]),
                "note": spec["note"],
            }
            for name, spec in BODY_PARTS_19_TO_52.items()
        }

    @staticmethod
    def _resize_mask(mask: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
        pil = Image.fromarray(mask.astype(np.uint8) * 255, "L").resize(size, Image.NEAREST)
        return np.array(pil) > 0

    # ------------------------------------------------------------------- layer

    def layer(
        self,
        image: Image.Image,
        output_dir: Optional[str] = None,
        label_layers: bool = True,
        depth_map: Optional[np.ndarray] = None,
    ) -> Dict:
        """分割 + 导出，返回与 :meth:`SemanticSegmenter.layer` 相同的结构。

        额外键（``parts_report`` / ``missing_parts`` / ``detected_parts`` /
        ``mapping`` / ``elapsed_s`` 等）是纯增量，``core/workflow.py`` 不读它们。
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        out = Path(output_dir) if output_dir else Path.cwd() / "output" / f"layers_{int(time.time())}"
        out.mkdir(parents=True, exist_ok=True)

        masks, report = self.segment_detailed(image)
        method = str(report["method"])

        if not masks:
            log.error(f"{method}: 19 类全部为空，返回显式失败（不降级到颜色启发式）")
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

        from core.segment_engine.composer import LayerComposer

        try:
            composer = LayerComposer(device=self.device)
            composed = composer.compose(masks, image, str(out), depth_map=depth_map)
            ordered = composer.reorder_layers(composed)
            composition = "layer_composer"
        except Exception as exc:  # pragma: no cover - 合成失败时直接烧掩码
            log.warning(f"LayerComposer 失败（{exc}）；改为直接导出掩码")
            ordered = self._direct_mask_export(masks, image, out)
            composer = LayerComposer(device=self.device)
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
            })

        preview_path = out / "preview.png"
        image.save(preview_path)
        composite_path = out / "composite_preview.png"
        from core.segment_engine.semantic import SemanticSegmenter
        SemanticSegmenter._write_composite_preview(exported_layers, image.size, composite_path)

        guide_path = out / "layer_guide.json"
        try:
            composer.generate_layer_json(dict(ordered), str(guide_path))
        except Exception as exc:  # pragma: no cover - guide 只是附带产物
            log.warning(f"layer_guide.json 未写出: {exc}")
            guide_path = None  # type: ignore[assignment]

        if report["missing_parts"]:
            log.warning(
                f"{method}: {len(report['missing_parts'])} 个部位模型未给出: "
                f"{', '.join(report['missing_parts'])}"
            )
        log.success(
            f"{method}: {len(exported_layers)}/{CLASS_NUM} 层已导出 "
            f"({composition}) -> {out}"
        )
        return {
            "success": True,
            "method": method,
            "layers": exported_layers,
            "output_dir": str(out),
            "preview_path": str(preview_path),
            "composite_preview": str(composite_path),
            "layer_count": len(exported_layers),
            "k_clusters": CLASS_NUM,
            "segmentation_mask": None,
            "guide_path": guide_path,
            **report,
        }

    @staticmethod
    def _direct_mask_export(
        masks: Dict[str, np.ndarray],
        image: Image.Image,
        out: Path,
    ) -> Dict[str, Dict]:
        """合成器不可用时的直出：每个 mask 烧一张 RGBA。"""
        arr = np.array(image.convert("RGBA"))
        exported: Dict[str, Dict] = {}
        for part, mask in masks.items():
            mask = np.asarray(mask, dtype=bool)
            layer = np.zeros_like(arr)
            layer[mask] = arr[mask]
            path = out / f"{part}.png"
            Image.fromarray(layer, "RGBA").save(path)
            ys, xs = np.nonzero(mask)
            exported[part] = {
                "name": part,
                "path": str(path),
                "bbox": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                "size": (int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)),
                "pixel_count": int(mask.sum()),
                "mean_color": tuple(int(c) for c in arr[mask][:, :3].mean(axis=0)),
                "occluded_by": [],
                "completed": False,
            }
        return exported
