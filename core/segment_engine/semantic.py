#!/usr/bin/env python3
"""
Live2D Master Agent - Semantic Segmentation Engine

Segments an anime character image into the 18 standard Live2D parts using
one of several backends (ISNet/anime-segmentation, SAM, or rembg), with
graceful fallbacks to a HSV color-based heuristic when no learned model is
available. Returns a dict mapping part name -> boolean mask.

The 18 standard parts (see :attr:`SemanticSegmenter.STANDARD_PARTS`):
    hair_back, hair_front, face, eyebrows, eyes_left, eyes_right, nose,
    mouth, neck, clothes_top, clothes_bottom, arms, hands, legs,
    accessories, plus background/skin/other as helpers.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger
from core.segment_engine import face_detect, isnet_onnx
from core.utils.image_utils import estimate_foreground_silhouette

log = get_logger("segment.semantic")

# Optional dependencies
try:
    import cv2  # type: ignore
    HAS_CV2 = True
except ImportError:  # pragma: no cover
    HAS_CV2 = False

try:
    import torch  # type: ignore
    HAS_TORCH = True
except ImportError:  # pragma: no cover
    HAS_TORCH = False

try:
    import rembg  # type: ignore
    HAS_REMBG = True
except ImportError:  # pragma: no cover
    HAS_REMBG = False


class SemanticSegmenter:
    """Semantic segmentation of anime character images into Live2D parts.

    Args:
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``.
        model_type: Backend selector:
            - ``"isnet"`` (default) — SkyTNT/anime-segmentation style ISNet
            - ``"sam"`` — Segment Anything (supports anime-finetuned weights)
            - ``"rembg"`` — rembg/U2Net (fast, foreground only; parts derived
              from heuristics)
            - ``"sam2_gd"`` — GroundingDINO (text -> boxes) + SAM2 (boxes ->
              masks). Delegates to
              :class:`core.segment_engine.sam2_gd.Sam2GroundingDinoSegmenter`
              and raises ``ModelUnavailable`` instead of using the HSV
              fallback below.
    """

    STANDARD_PARTS: List[str] = [
        "hair_back",
        "hair_front",
        "face",
        "eyebrows",
        "eyes_left",
        "eyes_right",
        "nose",
        "mouth",
        "neck",
        "clothes_top",
        "clothes_bottom",
        "arms",
        "hands",
        "legs",
        "accessories",
    ]

    # Mapping from rough semantic region (hair/skin/eyes/etc.) to Live2D
    # parts. SAM/ISNet typically produce per-component masks that are then
    # classified by position+color+shape into these names.
    PART_PRIORITY: List[str] = [
        "eyes_left", "eyes_right", "eyebrows", "nose", "mouth",
        "face", "hair_front", "hair_back", "neck",
        "hands", "arms", "clothes_top", "clothes_bottom", "legs",
        "accessories",
    ]

    def __init__(self, device: str = "auto", model_type: str = "isnet") -> None:
        self.device = self._resolve_device(device)
        self.model_type = self._resolve_backend((model_type or "isnet").lower())
        self._model = None
        self._model_loaded = False
        # segment() 每次运行都会重置这几个标记；这里先置默认，避免调用方在
        # 首次 segment() 之前读取时抛 AttributeError。
        self._last_was_fallback = False
        self._last_was_fg_gated = False
        #: 无学习式模型时，前景剪影是否由画面边框颜色自估（见
        #: :meth:`_synthesise_foreground`）。仅用于诊断/日志。
        self._last_fg_synthesised = False
        #: 前景掩码来源：onnx_local / rembg / anime_seg / synthesised / none
        self._last_fg_source = "none"
        #: 脸部定位来源：yunet / skin_heuristic / none
        self._last_face_source = "none"
        #: 脸部包围盒 (x0, y0, x1, y1)，像素坐标
        self._face_box: Optional[Tuple[int, int, int, int]] = None
        #: 模型加载时确定的前景来源（模型只加载一次，跨多次 segment 复用）
        self._fg_source = "none"
        log.info(f"SemanticSegmenter initialized (backend={self.model_type}, device={self.device})")

    def _resolve_backend(self, model_type: str) -> str:
        """``auto`` 优先选择能产出「部位级」掩码的后端。

        SAM 一次输出多个实例掩码，可细分为头发/眼睛/嘴等部位；ISNet/rembg
        只输出单个前景掩码，无法拆分部位（实测仅 1~2 个部位）。因此 ``auto``
        在检测到 SAM 及其权重可用时选 ``sam``，否则退回 ``isnet``。
        """
        if model_type != "auto":
            return model_type
        if HAS_TORCH and self._find_sam_checkpoint() is not None:
            try:
                import segment_anything  # noqa: F401
                return "sam"
            except Exception:
                pass
        return "isnet"

    def _find_sam_checkpoint(self) -> Optional[tuple]:
        """定位可用的 SAM 权重，返回 ``(checkpoint_path, model_type)``。"""
        import glob

        patterns = [
            ("~/.cache/huggingface/hub/models--anime-segmentation--sam-vit-huge-anime/snapshots/*/sam_vit_h_anime.pth", "vit_h"),
            ("~/.cache/anime-segmentation/sam_vit_h_anime.pth", "vit_h"),
            ("~/models/sam_vit_h_anime.pth", "vit_h"),
        ]
        for pat, mt in patterns:
            m = glob.glob(os.path.expanduser(pat))
            if m:
                return m[0], mt
        for alt, mt in (("sam_vit_b_01ec64.pth", "vit_b"), ("sam_vit_l_0b3195.pth", "vit_l")):
            cand = os.path.expanduser(f"~/.cache/sam/{alt}")
            if os.path.isfile(cand):
                return cand, mt
        return None

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device == "auto":
            if HAS_TORCH and torch.cuda.is_available():  # type: ignore[name-defined]
                return "cuda"
            return "cpu"
        return device

    # ------------------------------------------------------------------ public

    def segment(self, image: Image.Image) -> Dict[str, np.ndarray]:
        """Segment ``image`` into per-part boolean masks.

        Args:
            image: PIL Image (any mode; converted to RGBA internally).

        Returns:
            Dict mapping part name -> ``np.uint8`` or boolean mask.
            Only parts with non-empty masks are included.

        Raises:
            core.segment_engine.sam2_gd.ModelUnavailable: for
                ``model_type="sam2_gd"`` when torch/CUDA/weights are missing.
                That backend never degrades into the HSV fallback below.
        """
        if self.model_type == "sam2_gd":
            from core.segment_engine.sam2_gd import Sam2GroundingDinoSegmenter
            return Sam2GroundingDinoSegmenter(device=self.device).segment(image)

        if image.mode != "RGBA":
            image = image.convert("RGBA")
        img_arr = np.array(image)
        h, w = img_arr.shape[:2]

        # Try the configured model first.
        masks: List[Dict] = []
        self._last_was_fallback = False
        self._last_was_fg_gated = False
        self._last_fg_synthesised = False
        try:
            if self.model_type == "isnet":
                masks = self._segment_isnet(image)
            elif self.model_type == "sam":
                masks = self._segment_sam(image)
            elif self.model_type == "rembg":
                masks = self._segment_rembg(image)
            else:
                log.warning(f"Unknown model_type '{self.model_type}'; falling back")
        except Exception as exc:
            log.warning(f"Backend '{self.model_type}' failed: {exc}")

        if not masks:
            log.info("No learned masks produced; using HSV color fallback")
            self._last_was_fallback = True
            return self._fallback_color_segment(image)

        if len(masks) == 1 and str(masks[0].get("label", "")) == "foreground":
            # 后端只给了一张前景掩码（rembg 的 ISNet 就是这种），不含任何部件
            # 信息。逐掩码分类会把整张角色判成 hair（实测部件从 11 退化到 2 个
            # 完全重叠的 hair 层），所以这里改用它当 gate：颜色分割照常跑，再与
            # 前景求交 —— 既保留多部件，又用学习式掩码裁掉背景杂色。
            self._last_was_fg_gated = True
            fg = masks[0].get("segmentation")
            if fg is not None:
                fg = np.asarray(fg)
                fg = fg > 0 if fg.dtype != bool else fg
                if fg.shape != (h, w):
                    fg = np.array(
                        Image.fromarray(fg.astype(np.uint8) * 255, "L")
                        .resize((w, h), Image.NEAREST)
                    ) > 0
            # 把前景**传进**颜色分割：不传的话背景会被当成头发/衣服参与
            # 发色采样和腰线定位（实测腰线被背景带偏到腿上），最后虽然还会
            # 与前景求交，但内部边界已经错了，腿/裙摆这类分层会整体串位。
            parts = self._fallback_color_segment(image, fg)
            if fg is not None and fg.any():
                parts = {
                    name: (np.asarray(m, dtype=bool) & fg)
                    for name, m in parts.items() if m is not None
                }
        else:
            try:
                parts = self._classify_masks_anime(masks, np.array(image.convert("RGB")))
            except Exception as exc:
                log.warning(f"Mask classification failed ({exc}); using fallback")
                self._last_was_fallback = True
                return self._fallback_color_segment(image)

        # Validate shapes
        clean: Dict[str, np.ndarray] = {}
        for name, m in parts.items():
            if m is None:
                continue
            m_arr = np.asarray(m, dtype=bool)
            if m_arr.shape != (h, w):
                log.debug(f"Resizing mask '{name}' from {m_arr.shape} to {(h, w)}")
                m_pil = Image.fromarray(m_arr.astype(np.uint8) * 255, "L").resize((w, h), Image.NEAREST)
                m_arr = np.array(m_pil) > 0
            if m_arr.any():
                clean[name] = m_arr
        # If classification produced nothing useful, fall back.
        if not clean:
            self._last_was_fallback = True
            return self._fallback_color_segment(image)
        return clean

    def layer(
        self,
        image: Image.Image,
        output_dir: Optional[str] = None,
        label_layers: bool = True,
        depth_map: Optional[np.ndarray] = None,
    ) -> Dict:
        """Segment ``image`` into Live2D parts and export RGBA layer PNGs.

        This is the adapter method used by :class:`core.workflow.WorkflowEngine`
        to make the semantic segmenter interchangeable with
        :class:`core.segment_engine.kmeans.KMeansLayerer`. It calls
        :meth:`segment` to obtain boolean masks, then uses
        :class:`core.segment_engine.composer.LayerComposer` to perform
        amodal completion, RGBA extraction, and PNG export, and finally
        returns a dict whose shape matches ``KMeansLayerer.layer()``.

        Args:
            image: Source PIL Image (any mode; converted to RGBA).
            output_dir: Directory to write layer PNGs + preview + guide.
                Falls back to a timestamped ``layers_<ts>`` directory.
            label_layers: Accepted for API parity; currently unused.

        Returns:
            Dict with keys: ``success``, ``method``, ``layers`` (list of
            dicts with ``index``/``name``/``path``/``pixel_count``/``size``),
            ``output_dir``, ``preview_path``, ``composite_preview``,
            ``layer_count``, ``k_clusters``, ``segmentation_mask``.

        Raises:
            core.segment_engine.sam2_gd.ModelUnavailable: for
                ``model_type="sam2_gd"`` when the models cannot be loaded.
        """
        if self.model_type == "sam2_gd":
            from core.segment_engine.sam2_gd import Sam2GroundingDinoSegmenter
            return Sam2GroundingDinoSegmenter(device=self.device).layer(
                image, output_dir=output_dir, label_layers=label_layers, depth_map=depth_map
            )

        if image.mode != "RGBA":
            image = image.convert("RGBA")

        if output_dir:
            out = Path(output_dir)
        else:
            out = Path.cwd() / "output" / f"layers_{int(time.time())}"
        out.mkdir(parents=True, exist_ok=True)

        # 1. Run segmentation -> boolean masks per part
        t0 = time.time()
        masks = self.segment(image)
        is_fallback = getattr(self, "_last_was_fallback", False)
        if getattr(self, "_last_was_fg_gated", False):
            # 部件仍来自颜色启发式，只是剪影由学习式前景掩码裁出，比纯 HSV
            # 回退准，但不是部件级学习分割——如实单独标注，不冒充 "semantic"。
            method = "semantic_fg_gated_color"
        elif is_fallback:
            method = "semantic_hsv_fallback"
        else:
            method = "semantic"

        if not masks:
            log.warning("Semantic segmentation produced no masks; returning empty result")
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
            }

        # 2. Use LayerComposer to do amodal completion + ordered PNG export
        try:
            from core.segment_engine.composer import LayerComposer
            composer = LayerComposer(device=self.device)
            composed = composer.compose(masks, image, str(out), depth_map=depth_map)
            ordered = composer.reorder_layers(composed)
        except Exception as exc:
            log.warning(f"LayerComposer failed ({exc}); falling back to direct mask export")
            ordered = self._direct_mask_export(masks, image, out)

        # 3. Convert to KMeans-compatible layer list
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

        # 4. Save preview (original optimized image) and composite preview
        preview_path = out / "preview.png"
        image.save(preview_path)

        composite_preview_path = out / "composite_preview.png"
        self._write_composite_preview(exported_layers, image.size, composite_preview_path)

        # 5. Write layer JSON guide via composer
        guide_path = out / "layer_guide.json"
        try:
            composer_path_cls = None
            from core.segment_engine.composer import LayerComposer as _LC
            composer_path_cls = _LC
            if composer_path_cls is not None and "composed" in dir():
                composer.generate_layer_json(composed if composed else ordered, str(guide_path))
        except Exception:
            pass

        elapsed = time.time() - t0
        log.success(
            f"Semantic layering complete: {len(exported_layers)} layers "
            f"(method={method}) in {elapsed:.2f}s -> {out}"
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
            "guide_path": str(guide_path) if guide_path.exists() else None,
        }

    def _direct_mask_export(
        self,
        masks: Dict[str, np.ndarray],
        image: Image.Image,
        out: Path,
    ) -> Dict[str, Dict]:
        """Fallback: directly burn each boolean mask into a transparent PNG.

        Used when LayerComposer / amodal completion raises (e.g. missing
        optional deps). Returns the same dict shape as
        :meth:`LayerComposer.compose`.
        """
        from collections import OrderedDict
        img_arr = np.array(image.convert("RGBA"))
        h, w = img_arr.shape[:2]
        ordered_names = [n for n in self.STANDARD_PARTS if n in masks] + \
                        sorted(n for n in masks if n not in self.STANDARD_PARTS)
        result: "OrderedDict[str, Dict]" = OrderedDict()
        for idx, name in enumerate(ordered_names):
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

    @staticmethod
    def _write_composite_preview(
        layers: List[Dict],
        size: tuple,
        path: Path,
    ) -> None:
        """Alpha-composite all exported layers onto a transparent canvas."""
        composite = Image.new("RGBA", size, (0, 0, 0, 0))
        for info in layers:
            try:
                layer_img = Image.open(info["path"]).convert("RGBA")
                composite = Image.alpha_composite(composite, layer_img)
            except Exception:
                continue
        composite.save(path)

    # --------------------------------------------------------------- backends

    def _load_isnet(self):
        """加载前景/部位分割模型。

        优先级（**全程不联网**）：

        1. 本地 ONNX 权重 —— 桌面安装包内置 ``models/rembg/isnet-anime.onnx``，
           直接用 onnxruntime 跑。不走 rembg，因此不会触发它的隐式下载。
        2. ``anime_segmentation`` 包 —— 输出部位级掩码（质量最高），但依赖
           torch，首次使用需要权重。
        3. 都没有 —— 明确降级，不假装有模型。

        历史坑：``rembg.new_session()`` 在本地没有权重时会**静默联网下载**
        176MB（ISNet）。桌面安装包在离线环境或国内网络下必然失败，于是分层
        悄悄退化成颜色启发式，用户看到的分层是坏的。现在改用本地权重运行，
        找不到就明说，绝不偷偷联网。
        """
        if self._model_loaded:
            return
        self._model_loaded = True

        # 1) 本地 ONNX 权重（桌面安装包内置）
        model_path = isnet_onnx.find_model()
        if model_path is not None:
            try:
                self._model = isnet_onnx.OnnxForegroundModel(
                    model_path, providers=self._ort_providers()
                )
                self._fg_source = "onnx_local"
                log.success(f"Loaded local ONNX foreground model: {model_path}")
                return
            except Exception as exc:
                log.warning(f"Local ONNX foreground model failed: {exc}")
        else:
            log.info(
                "No local foreground model found; will try anime_segmentation package"
            )

        # 2) anime_segmentation 包（部位级掩码）
        try:  # pragma: no cover - heavy optional dep
            from anime_segmentation import get_isnet_model  # type: ignore
            self._model = get_isnet_model(device=self.device)
            self._fg_source = "anime_seg"
            log.success("Loaded ISNet model from anime_segmentation package")
            return
        except ImportError:
            log.debug("anime_segmentation package not available")
        except Exception as exc:
            log.warning(f"anime_segmentation load failed: {exc}")

        log.warning(
            "No foreground segmentation model available. Layering will fall back "
            "to HSV color heuristics gated by a self-estimated silhouette "
            "(preview quality only). To enable the learned model, place "
            "isnet-anime.onnx under the bundled 'models/rembg' directory, or "
            "install the anime-segmentation package."
        )
        self._model = None

    def _ort_providers(self) -> List[str]:
        """onnxruntime 执行提供方列表（优先 GPU，回落 CPU）。"""
        if not isnet_onnx.HAS_ORT:  # pragma: no cover
            return ["CPUExecutionProvider"]
        try:
            available = isnet_onnx.ort.get_available_providers()
        except Exception:  # pragma: no cover
            return ["CPUExecutionProvider"]
        if self.device == "cuda" and "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    def _onnx_provider(self) -> str:
        return "CUDAExecutionProvider" if self.device == "cuda" else "CPUExecutionProvider"

    def _load_sam(self):
        """Load a Segment Anything model, preferring anime-finetuned weights."""
        if self._model_loaded:
            return
        self._model_loaded = True
        if not HAS_TORCH:
            log.warning("torch not installed; cannot load SAM")
            return
        try:  # pragma: no cover - heavy optional dep
            from segment_anything import sam_model_registry, SamAutomaticMaskGenerator  # type: ignore
            found = self._find_sam_checkpoint()
            if found is None:
                log.warning("No SAM checkpoint found; place one under ~/.cache/sam/ to enable SAM")
                self._model = None
                return
            ckpt, model_type = found
            sam = sam_model_registry[model_type](checkpoint=ckpt)
            sam.to(device=self.device)
            # CPU 上默认 points_per_side=32 会生成 1024 个提示点，单图耗时可达数分钟；
            # 按设备自适应采样密度，兼顾耗时与部位召回。
            is_cpu = str(self.device) == "cpu"
            self._model = SamAutomaticMaskGenerator(
                sam,
                points_per_side=8 if is_cpu else 32,
                pred_iou_thresh=0.80,
                stability_score_thresh=0.85,
                crop_n_layers=0,
                min_mask_region_area=200,
            )
            log.success(f"Loaded SAM ({model_type}) from {ckpt} on {self.device}")
        except ImportError:
            log.warning("segment_anything not installed")
            self._model = None
        except Exception as exc:
            log.warning(f"SAM load failed: {exc}")
            self._model = None

    # -------------------------------------------------------- backend runners

    def _segment_isnet(self, image: Image.Image) -> List[Dict]:
        """Run the foreground/part segmentation model, returning SAM-style dicts."""
        self._load_isnet()
        if self._model is None:
            return []
        model = self._model

        try:
            pred = model.predict(image)
        except Exception as exc:
            log.warning(f"Foreground model predict failed: {exc}")
            return []

        # anime_segmentation：部位级掩码（label -> mask）
        if isinstance(pred, dict):
            out: List[Dict] = []
            for key, mask in pred.items():
                arr = np.asarray(mask).astype(bool)
                if arr.shape != (image.height, image.width):
                    arr = self._resize_mask(arr, image.size)
                if arr.any():
                    out.append({"segmentation": arr, "label": str(key)})
            if out:
                self._last_fg_source = self._fg_source or "anime_seg"
            return out

        # 本地 ONNX 运行时：直接返回前景布尔掩码
        if isinstance(pred, np.ndarray):
            arr = np.asarray(pred).astype(bool)
            if arr.shape != (image.height, image.width):
                arr = self._resize_mask(arr, image.size)
            if arr.any():
                self._last_fg_source = self._fg_source or "onnx_local"
                return [{"segmentation": arr, "label": "foreground"}]
            return []

        # rembg 风格会话：返回 RGBA 抠图列表
        if isinstance(pred, list) and pred and isinstance(pred[0], Image.Image):
            arr = np.array(pred[0].convert("RGBA"))
            fg = arr[:, :, 3] > 0
            if fg.any():
                self._last_fg_source = self._fg_source or "rembg"
                return [{"segmentation": fg, "label": "foreground"}]
        return []

    @staticmethod
    def _resize_mask(mask: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
        """把掩码缩放到目标 ``(width, height)``。"""
        width, height = int(size[0]), int(size[1])
        pil = Image.fromarray(mask.astype(np.uint8) * 255, "L").resize(
            (width, height), Image.NEAREST
        )
        return np.array(pil) > 0

    def _segment_sam(self, image: Image.Image) -> List[Dict]:
        """Run SAM automatic mask generation."""
        self._load_sam()
        if self._model is None:
            return []
        rgb = np.array(image.convert("RGB"))
        try:  # pragma: no cover - external
            masks = self._model.generate(rgb)
            # SAM masks already have: segmentation (bool HxW), bbox, area, etc.
            return masks
        except Exception as exc:
            log.warning(f"SAM inference failed: {exc}")
            return []

    def _segment_rembg(self, image: Image.Image) -> List[Dict]:
        """Run rembg foreground extraction (returns a single mask)."""
        if not HAS_REMBG:
            return []
        try:  # pragma: no cover - optional
            cutout = rembg.remove(image)  # type: ignore[union-attr]
            if cutout.mode != "RGBA":
                cutout = cutout.convert("RGBA")
            fg = (np.array(cutout)[:, :, 3] > 0).astype(bool)
            return [{"segmentation": fg, "label": "foreground"}] if fg.any() else []
        except Exception as exc:
            log.warning(f"rembg inference failed: {exc}")
            return []

    # -------------------------------------------------------- classification

    def _classify_masks_anime(
        self,
        masks: List[Dict],
        image: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Classify raw SAM/ISNet masks into the 15 anime-part set.

        Uses position (centroid), color (mean HSV/RGB), shape (aspect, area),
        and simple geometric constraints. Left/right eyes and eyebrows are
        split by x-centroid.
        """
        h, w = image.shape[:2]
        parts: Dict[str, np.ndarray] = {p: np.zeros((h, w), dtype=bool) for p in self.STANDARD_PARTS}

        # Sort masks by area descending (large regions first)
        norm_masks: List[Dict] = []
        for m in masks:
            seg = np.asarray(m.get("segmentation"), dtype=bool)
            if seg.shape != (h, w):
                # resize if necessary
                seg_pil = Image.fromarray(seg.astype(np.uint8) * 255, "L").resize((w, h), Image.NEAREST)
                seg = np.array(seg_pil) > 0
            if not seg.any():
                continue
            area = int(seg.sum())
            ys, xs = np.where(seg)
            cx = float(xs.mean()) / w
            cy = float(ys.mean()) / h
            mean_rgb = image[seg].mean(axis=0) if area else np.array([128, 128, 128])
            norm_masks.append({
                "seg": seg,
                "area": area,
                "area_ratio": area / float(h * w),
                "cx": cx,
                "cy": cy,
                "mean_rgb": mean_rgb,
                "bbox": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                "h_span": (ys.max() - ys.min()) / h,
                "w_span": (xs.max() - xs.min()) / w,
            })
        norm_masks.sort(key=lambda d: d["area"], reverse=True)

        if not norm_masks:
            return {}

        # Estimate face bounding box: the mask closest to vertical center that
        # has skin-toned colors.
        face_region = self._guess_face_region(norm_masks, image)
        fx0, fy0, fx1, fy1 = face_region
        face_mask = np.zeros((h, w), dtype=bool)
        face_mask[fy0:fy1 + 1, fx0:fx1 + 1] = True

        for m in norm_masks:
            name = self._classify_single(m, face_region, image)
            if name in ("eyes_left", "eyes_right", "eyebrows"):
                # split left/right by x centroid relative to face center
                fcx = (fx0 + fx1) / 2.0 / w
                if name == "eyebrows":
                    target = "eyebrows"  # both brows share a mask but add individually
                    parts[target] |= m["seg"]
                    continue
                if m["cx"] < fcx:
                    target = "eyes_left" if name == "eye" else "eyebrows_left"
                else:
                    target = "eyes_right" if name == "eye" else "eyebrows_right"
                if target == "eyebrows_left":
                    parts["eyebrows"] |= m["seg"]
                elif target == "eyebrows_right":
                    parts["eyebrows"] |= m["seg"]
                else:
                    parts[target] |= m["seg"]
                continue
            if name == "eye":
                # Unknown laterality
                target = "eyes_left" if m["cx"] < (fx0 + fx1) / 2.0 / w else "eyes_right"
                parts[target] |= m["seg"]
                continue
            if name in parts:
                parts[name] |= m["seg"]
            elif name == "background":
                continue
            else:
                # Lump unknown into accessories
                parts["accessories"] |= m["seg"]

        # If hair_back/hair_front not separated, split hair mask top vs bangs:
        if not parts["hair_back"].any() and parts["hair_front"].any():
            parts["hair_back"] = parts["hair_front"].copy()
        if not parts["hair_front"].any() and parts["hair_back"].any():
            parts["hair_front"] = parts["hair_back"].copy()

        # Strip empty parts
        return {k: v for k, v in parts.items() if v.any()}

    def _classify_single(
        self,
        m: Dict,
        face_region: tuple,
        image: np.ndarray,
    ) -> str:
        """Heuristically label one mask with a part name."""
        fx0, fy0, fx1, fy1 = face_region
        fh = max(1, fy1 - fy0)
        fw = max(1, fx1 - fx0)
        cx, cy = m["cx"], m["cy"]
        ar = m["area_ratio"]
        rgb = m["mean_rgb"]
        brightness = float(rgb.mean())

        # Eye-like: small, near vertical center of face, on face, colorful/white/black
        on_face_x = fx0 / max(1, image.shape[1]) <= cx <= fx1 / max(1, image.shape[1])
        on_face_y = fy0 / max(1, image.shape[0]) <= cy <= fy1 / max(1, image.shape[0])
        # Normalize cx/cy to face coords
        face_h = image.shape[0]
        face_w = image.shape[1]
        fy_center = (fy0 + fy1) / 2.0 / face_h
        # Eyes: ~10-30% down from top of face
        rel_y = (cy - fy0 / face_h) / max(1e-6, (fy1 - fy0) / face_h)

        # Mouth: low in face area, small
        if 0.65 < rel_y < 1.0 and on_face_x and ar < 0.02 and ar > 0.0005:
            return "mouth"
        # Nose: tiny, middle of face
        if 0.45 < rel_y < 0.75 and on_face_x and ar < 0.008:
            return "nose"
        # Eyes: around 0.3-0.55 down from face top, small-ish
        if 0.25 < rel_y < 0.6 and on_face_x and 0.0005 < ar < 0.05:
            return "eye"
        # Eyebrows: above eyes
        if 0.05 < rel_y < 0.35 and on_face_x and ar < 0.02 and m["h_span"] < 0.1:
            return "eyebrows"
        # Face: large skin-toned region overlapping face bbox
        if ar > 0.03 and self._is_skin_color(rgb) and on_face_x and 0.2 < cy < 0.7:
            return "face"

        # Neck: below face, narrow, skin color
        if cy > fy1 / face_h and self._is_skin_color(rgb) and m["w_span"] < 0.25 and ar < 0.08:
            return "neck"

        # Hair: top portion or surrounding face; non-skin; large
        if (cy < 0.45 or ar > 0.15) and not self._is_skin_color(rgb) and ar > 0.01:
            # Hair front = overlaps upper face; hair back = above/around
            if on_face_x and cy < fy_center and ar < 0.3:
                return "hair_front"
            return "hair_back"

        # Hands/arms: skin color away from face
        if self._is_skin_color(rgb):
            if cy > 0.55 and m["w_span"] < 0.12 and ar < 0.04:
                return "hands"
            if 0.45 < cy < 0.9 and ar < 0.15:
                return "arms"

        # Legs: lower third, elongated vertical, skin or dark
        if cy > 0.7 and m["h_span"] > m["w_span"] and ar < 0.25:
            return "legs"

        # Clothes: large, non-skin, middle-to-lower body
        if ar > 0.05 and cy > 0.45:
            if cy < 0.7:
                return "clothes_top"
            return "clothes_bottom"

        # Small non-skin features near hair are accessories
        if ar < 0.02 and brightness > 150:
            return "accessories"

        return "other"

    @staticmethod
    def _is_skin_color(rgb: np.ndarray) -> bool:
        """Very loose anime skin-tone test in RGB."""
        r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
        if r < 120 or g < 80 or b < 70:
            return False
        if r < g or r < b:
            return False
        if (r - g) < 8:
            return False
        return True

    def _guess_face_region(self, masks: List[Dict], image: np.ndarray) -> tuple:
        """估计脸部包围盒 (x0,y0,x1,y1)。

        优先用 YuNet 真实检测（桌面安装包内置权重），失败才退到肤色连通域，
        最后才用经验框。旧实现只会从掩码里猜皮肤块，导致脸框整体下移、
        中线偏移，眼睛与嘴被切到下巴上。

        ``face_detect.detect_face`` 现在返回带 5 点关键点的 :class:`FaceAnchors`，
        这里只取 ``box``；定位来源照常记到 ``_last_face_source``。
        """
        h, w = image.shape[:2]
        union = np.zeros((h, w), dtype=bool)
        for m in masks:
            seg = np.asarray(m.get("segmentation"), dtype=bool)
            if seg.shape == (h, w):
                union |= seg
        anchors = face_detect.detect_face(image, union if union.any() else None)
        if anchors is not None:
            self._last_face_source = anchors.source
            return anchors.box
        self._last_face_source = face_detect.SOURCE_NONE
        return (int(w * 0.25), int(h * 0.18), int(w * 0.75), int(h * 0.65))

    # ---------------------------------------------------------- color fallback

    #: 背景调色板比色容差（RGB 欧氏距离，0~255）。26 是实测值：再小会把
    #: 背景墙的明暗渐变留在前景里，再大开始把深色鞋/藏青裙的边吃掉。
    SILHOUETTE_COLOR_TOLERANCE = 26

    @staticmethod
    def _alpha_carries_silhouette(image: Image.Image) -> bool:
        """alpha 通道是否真的描述了剪影。

        流水线会把图统一转成 RGBA（``normalize_image``），所以
        ``image.mode == "RGBA"`` 本身**不等于**"带透明背景"：整图不透明时
        按 alpha 取前景得到的还是"整张画布"，背景墙照样被算进头发。
        """
        alpha = np.array(image.convert("RGBA"))[:, :, 3]
        return float((alpha <= 10).mean()) >= 0.02

    def _synthesise_foreground(self, rgb: np.ndarray) -> np.ndarray:
        """无学习式前景模型时，从画面自身估算角色剪影。

        实现在 :func:`core.utils.image_utils.estimate_foreground_silhouette`：
        优化图（背景移除）与分层两条路必须用**同一张**剪影，否则会出现
        "优化图把背景留成不透明、分层再按透明通道取前景"的互相打架。

        旧路径在没有模型、也没有透明背景时直接 ``fg = 全 True``：立绘的深色
        网格墙与头发同色系，实测 ``hair_back`` 覆盖 68% 画布、bbox 铺满全图；
        压在画面上的白色标注文字又被切进 ``clothes_top``，把上衣打成 204 个
        碎块。
        """
        return estimate_foreground_silhouette(
            rgb, tolerance=self.SILHOUETTE_COLOR_TOLERANCE
        )

    def _fallback_color_segment(
        self,
        image: Image.Image,
        fg: Optional[np.ndarray] = None,
    ) -> Dict[str, np.ndarray]:
        """在**前景剪影内**做颜色启发式部件拆分。

        旧实现直接在整幅图上按颜色切：立绘背景（深色网格墙）与角色同色，
        背景被整片算成头发，实测头发占 46%、脸只有 0.7%、眼睛完全缺失。
        现在所有部件都限制在前景剪影内，并用 YuNet 脸框定位五官。

        Args:
            image: 源图。
            fg: 前景布尔掩码（学习式模型产出）。为 None 时按 alpha / 合成剪影。
        """
        rgb = np.array(image.convert("RGB"))
        h, w = rgb.shape[:2]

        # ---------- 前景剪影 ----------
        if fg is not None:
            fg = np.asarray(fg, dtype=bool)
            if fg.shape != (h, w):
                fg = self._resize_mask(fg, image.size)
        elif image.mode == "RGBA" and self._alpha_carries_silhouette(image):
            fg = np.array(image)[:, :, 3] > 10
        else:
            fg = self._synthesise_foreground(rgb)
            self._last_fg_synthesised = True
            self._last_fg_source = "synthesised"
        if not fg.any():
            fg = np.ones((h, w), dtype=bool)

        # ---------- 色彩空间 ----------
        if HAS_CV2:
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        else:
            hsv = self._rgb_to_hsv(rgb)
        val = hsv[:, :, 2].astype(np.int32)

        R = rgb[:, :, 0].astype(np.int32)
        G = rgb[:, :, 1].astype(np.int32)
        B = rgb[:, :, 2].astype(np.int32)
        yy, xx = np.mgrid[0:h, 0:w]

        # 肤色：暖色且明显高于绿蓝；白色衣物 R≈G 会被排除
        skin = fg & (R > 150) & (G > 95) & (B > 75) & (R > G + 8) & (R > B)

        # ---------- 脸框 + 五官锚点（YuNet 5 点关键点优先）----------
        anchors = face_detect.detect_face(rgb, fg)
        if anchors is None:
            anchors = face_detect.estimate_anchors_from_box(
                (int(w * 0.38), int(h * 0.18), int(w * 0.62), int(h * 0.42))
            )
        self._last_face_source = anchors.source
        fx0, fy0, fx1, fy1 = anchors.box
        face_h = max(1, fy1 - fy0)
        face_w = max(1, fx1 - fx0)
        face_cx = (fx0 + fx1) / 2.0
        in_face = (xx >= fx0) & (xx <= fx1) & (yy >= fy0) & (yy <= fy1)

        # ---------- 脸 + 五官 ----------
        # 五官墨线：睫毛/眼白/瞳孔/嘴线/鼻线。
        # 这里**不能**用「脸的肤色区域 & 非肤色」：眼睛和嘴在严格肤色掩码里
        # 是「洞」，但这些洞在拓扑上不封闭（外侧紧挨头发），填洞填不上，五官
        # 会整体消失。改为以脸内肤色中位色为参考做距离变换，直接把墨线取出来。
        skin_ref = self._skin_reference(rgb, skin, in_face)
        ink = self._ink_mask(rgb, skin_ref)
        # 容差肤色：脸的阴影渐变也算皮肤，否则按列扫描五官时会被切碎
        skin_tol = (~ink) | skin

        eyes_left, eyes_right = self._eye_parts(anchors, skin_tol, fg, (h, w))
        eyebrows = self._eyebrow_parts(anchors, skin_tol, fg, (h, w))
        nose = self._nose_part(anchors, ink, (h, w))
        mouth = self._mouth_part(anchors, ink, (h, w))

        features = eyes_left | eyes_right | eyebrows | nose | mouth

        # 脸底图层：完整实心脸形（含被刘海盖住的部分），并按五官锚点切掉脖子
        face, chin_y = self._face_base(skin, features, anchors, fg, (h, w))
        if not face.any():
            # 冷色/深色角色：肤色没命中，退化为脸框内的非暗像素
            face = in_face & fg

        # ---------- 头发 ----------
        non_skin = fg & ~skin
        fg_rows = np.where(fg.any(axis=1))[0]
        fg_top = int(fg_rows.min()) if fg_rows.size else 0
        fg_bottom = int(fg_rows.max()) if fg_rows.size else h - 1
        body_h = max(1, fg_bottom - fg_top)

        # 身体中轴与半宽：用来把「中轴上的衣服」和「两侧垂下来的长发」分开。
        # 只靠发色距离分不开——深蓝发与藏蓝裙子离发色中位色一样远。
        centers = []
        halfs = []
        torso_lo = min(h - 1, fy1 + int((fg_bottom - max(fy1, fg_top)) * 0.05))
        torso_hi = min(h, fy1 + int((fg_bottom - max(fy1, fg_top)) * 0.55))
        for y in range(max(0, torso_lo), torso_hi):
            xs_row = np.where(fg[y])[0]
            if xs_row.size:
                lo_x, hi_x = float(xs_row.min()), float(xs_row.max())
                centers.append((lo_x + hi_x) / 2.0)
                halfs.append((hi_x - lo_x) / 2.0)
        body_cx = float(np.median(centers)) if centers else face_cx
        body_half = float(np.median(halfs)) * 0.75 if halfs else face_w * 0.9
        torso_core = np.abs(xx - body_cx) <= body_half

        # ---------- 腰线（先于头发计算）----------
        # 腰线只用**身体中轴**上的纵向颜色剖面：把手臂和两侧长发排除后，
        # 上衣→下装的换色是一道很干净的边；整行统计会被手臂和头发带偏
        # （实测腰线被推到裙摆以下，裙子整块算成上衣）。
        # 它还要供头发分段使用：同色（藏蓝）的发与裙只能靠「在中轴、且在
        # 腰线以下」来区分。
        waist_y = fg_top + int(body_h * 0.45)
        core_pixels = non_skin & torso_core
        core_counts = core_pixels.sum(axis=1)
        if core_counts.any():
            core_totals = np.stack(
                [np.where(core_pixels, rgb[:, :, c], 0).sum(axis=1) for c in range(3)],
                axis=1,
            )
            core_mean = core_totals / np.maximum(core_counts, 1)[:, None]
            core_diff = np.abs(np.diff(core_mean, axis=0)).sum(axis=1)
            lo = max(fg_top + int(body_h * 0.22), fy1 + int(face_h * 0.4))
            # 上界必须卡在「下装以下」之上：整条剖面里最大的颜色跳变其实是
            # 裙摆→腿（深藏蓝→肤色），不卡住就会被当成腰线，裙子于是留在
            # 腰线之上、被归进头发（头发层会跟着头动，视觉上是错的）。
            # 用眼睛间距做尺度：腰线在嘴线下方约 8 个眼距以内。
            waist_hi = min(
                fg_bottom,
                fg_top + int(body_h * 0.65),
                int(round(anchors.mouth_mid[1] + 8.0 * max(anchors.eye_distance, 1.0))),
            )
            hi = max(lo + 1, waist_hi)
            if hi > lo and core_diff[lo:hi].any():
                cand = lo + int(np.argmax(core_diff[lo:hi]))
                if core_counts[cand] > 0:
                    waist_y = cand

        # ---------- 头发 ----------
        hair = non_skin & (yy < fy0)  # 头顶之上必是头发
        sample = non_skin & (yy < fy0 + int(face_h * 0.5))
        if sample.any():
            # 用发色向下延伸（长发不会停在头顶）。阈值必须设上限：样本里混着
            # 发丝高光和描线，85 分位距离会被拉到 130+，乘 1.8 后约 240，
            # 等于「整张角色都算头发」（实测头发涨到 11%、衣服被全部吃掉）。
            ref = np.median(rgb[sample], axis=0).astype(np.float32)
            dist = np.sqrt(((rgb.astype(np.float32) - ref) ** 2).sum(axis=2))
            spread = float(np.percentile(dist[sample], 60))
            limit = min(max(55.0, spread * 1.6), 150.0)
            near = dist <= limit
            hair = hair | (non_skin & near & (val >= max(40, int(ref.max() * 0.45))))
        else:
            near = np.zeros((h, w), dtype=bool)

        # 发色的明暗跨度很大（暗部到高光离中位色 150+），单靠距离阈值抓不全，
        # 再补一条「蓝通道主导」：蓝发在暗部/高光里都满足 B 明显高于 R，
        # 而白衬衫（R≈G≈B）和肤色（R>B）都不满足。
        bluish = (B > R + 12) & (B > 90) & (B > G + 4)
        hair = hair | (non_skin & bluish)

        shoulder_y = fy1 + int(face_h * 0.9)
        # 同色发/裙的唯一分界：身体中轴、腰线以下的蓝色是下装，不是头发。
        # 中轴两侧垂下来的长发保留（长发的落点可以低到大腿）。
        hair = hair & ~(torso_core & (yy > waist_y) & (B > R + 12))
        hair = hair & ~features
        if not hair.any() or int(hair.sum()) > int(non_skin.sum()) * 0.85:
            # 发色没区分开（银发/纯黑发等）：退回按位置切
            hair = non_skin & (yy < shoulder_y)
            hair = hair & ~(torso_core & (yy > waist_y))
        hair = hair & ~features

        # 「蓝色主导」这条规则会把同色系的领带、裙褶一起抓进头发（实测领带
        # 整条进了 hair_back）。头发在拓扑上是**一整块**：从头顶一直连到发梢；
        # 领带/裙褶是独立小岛。所以只保留与"头顶头发"连通的部分，剩下的
        # 像素自然落回下面 non_skin & ~hair 的衣服分支。
        seed = hair & (yy < fy0)  # 头顶之上必是头发，用它当种子
        if seed.any() and HAS_CV2:
            count, labels = cv2.connectedComponents(hair.astype(np.uint8), 8)
            keep = np.unique(labels[seed])
            keep = keep[keep > 0]
            if keep.size:
                hair = np.isin(labels, keep)

        # 刘海 = 脸部上方、脸宽范围内的头发；其余为后发
        bang_zone = (
            (yy >= fy0 - int(face_h * 1.0))
            & (yy <= fy0 + int(face_h * 0.5))
            & (np.abs(xx - face_cx) <= face_w * 1.1)
        )
        hair_front = hair & bang_zone
        hair_back = hair & ~hair_front
        if not hair_front.any() or not hair_back.any():
            hair_front = hair.copy()
            hair_back = hair.copy()

        # ---------- 脖子 ----------
        # 以下颌（脸底肤色最低点）为基准，比用 YuNet 脸框下沿更贴近真实轮廓
        neck_top = int(max(chin_y, fy1 - int(face_h * 0.05)))
        neck_zone = (
            (yy > neck_top)
            & (yy < neck_top + int(face_h * 0.9))
            & (np.abs(xx - face_cx) <= face_w * 0.6)
        )
        neck = skin & neck_zone & ~face
        if not neck.any():
            neck = skin & (yy > neck_top) & (yy < neck_top + face_h) & (np.abs(xx - face_cx) <= face_w)

        # ---------- 衣服 ----------
        clothes = non_skin & ~hair & ~features
        clothes_top = clothes & (yy < waist_y)
        clothes_bottom = clothes & (yy >= waist_y)

        # 裙子下摆：下装行宽从「宽」（裙摆/裤腿）骤降到「窄」（袜子/鞋）的位置。
        # 袜子与裙子同属「下装」，不切开的话袜子会把整条裙子的像素抢走。
        # 阈值取 0.6 而不是 0.4：两条袜子合起来的宽度也能到裙摆的四成，
        # 用 0.4 时袜子会被当成「宽」的一段，下摆一路被推到脚踝。
        hem_y = None
        if clothes_bottom.any():
            widths = clothes_bottom.sum(axis=1)
            peak = int(widths[waist_y:].max()) if widths[waist_y:].size else 0
            if peak > 0:
                last_wide = None
                for y in range(waist_y, fg_bottom + 1):
                    if widths[y] >= peak * 0.6:
                        last_wide = y
                    elif last_wide is not None:
                        hem_y = last_wide + 1
                        break
        if hem_y is not None and hem_y <= waist_y:
            hem_y = None

        # ---------- 四肢 ----------
        # 手 vs 腿不能用腰线分：腰线是从衣服颜色跳变猜出来的，实测会被裙子
        # 下摆带到腿上（waist_y=543 > 腿的顶端 507），整条腿被判成手臂，
        # legs 层直接消失、arms 涨到 5%。改用「是否伸到身形下部」这一条：
        # 腿会一直延伸到脚，手臂通常只到胯部附近。
        skin_body = skin & ~face & ~neck
        arms = np.zeros((h, w), dtype=bool)
        hands = np.zeros((h, w), dtype=bool)
        legs = np.zeros((h, w), dtype=bool)
        accessories = np.zeros((h, w), dtype=bool)
        leg_depth = fg_top + int(body_h * 0.62)
        for comp, _area, cx, _cy in self._components(skin_body):
            rows = np.where(comp.any(axis=1))[0]
            if rows.size == 0:
                continue
            top, bottom = int(rows.min()), int(rows.max())
            if bottom <= fy0:
                # 完全在脸框上方：耳/头饰一类，不该算手臂
                accessories |= comp
                continue
            if bottom >= leg_depth:
                legs |= comp
            else:
                # 手臂：末端一小段是手
                cut = bottom - max(1, int((bottom - top + 1) * 0.15))
                arms |= comp & (yy < cut)
                hands |= comp & (yy >= cut)

        # 裙摆以下的非肤色（袜子/鞋）单独成层，不混进裙子
        legwear = np.zeros((h, w), dtype=bool)
        if hem_y is not None:
            legwear = clothes_bottom & (yy >= hem_y)
            clothes_bottom = clothes_bottom & (yy < hem_y)

        # 结构性图层去碎点：抗锯齿在发丝/描线边缘留下的大量 1~3 像素孤立点
        # 本身没有语义，却会让图层在 Live2D 里出现游离小点，也会让碎片化检查
        # 误判（实测 hair_back 264 块里 245 块都是这种碎屑）。
        # 除了绝对下限，还按部件自身面积设相对下限（见 _despeckle）：发丝
        # 描边会沿头发轮廓拉出一圈几百像素的细条，绝对阈值拦不住。
        # 五官层不做这一步——鼻子本来就只有 9 个像素。
        speck = max(6, int(0.00003 * h * w))
        relative = 0.004
        hair_back = self._despeckle(hair_back, speck, relative)
        hair_front = self._despeckle(hair_front, speck, relative)
        face = self._despeckle(face, speck, relative)
        neck = self._despeckle(neck, speck, relative)
        clothes_top = self._despeckle(clothes_top, speck, relative)
        clothes_bottom = self._despeckle(clothes_bottom, speck, relative)
        arms = self._despeckle(arms, speck, relative)
        hands = self._despeckle(hands, speck, relative)
        legs = self._despeckle(legs, speck, relative)
        legwear = self._despeckle(legwear, speck, relative)
        accessories = self._despeckle(accessories, speck, relative)

        out = {
            "hair_back": hair_back,
            "hair_front": hair_front,
            "face": face,
            "eyebrows": eyebrows,
            "eyes_left": eyes_left,
            "eyes_right": eyes_right,
            "nose": nose,
            "mouth": mouth,
            "neck": neck,
            "clothes_top": clothes_top,
            "clothes_bottom": clothes_bottom,
            "arms": arms,
            "hands": hands,
            "legs": legs,
            "legwear": legwear,
            "accessories": accessories,
        }
        return {k: v for k, v in out.items() if v.any()}

    # ------------------------------------------------------- numpy 辅助（掩码）

    @staticmethod
    def _face_base(
        skin: np.ndarray,
        features: np.ndarray,
        anchors,
        fg: np.ndarray,
        shape: Tuple[int, int],
    ) -> Tuple[np.ndarray, int]:
        """生成「脸底图层」：完整脸形，而不是零散的可见肤色。

        为什么不能直接用肤色掩码当脸：刘海会在额头把皮肤切成左右两半、
        眼睛本身在肤色里是洞，直接输出的脸是**带窟窿的碎块**，贴到 Live2D
        里一转角度就露底。正确做法是先闭运算把刘海跨度内的空隙桥接起来、
        再填洞得到实心脸形，最后按下颌线切掉脖子。

        闭运算核按脸宽比例取（刘海宽度随头身比变化），并用「肤色附近」
        的掩码收紧外沿，避免把两侧的头发一起吞进脸里。

        Returns:
            ``(face, chin_y)``；``chin_y`` 为下颌行号，供脖子分区使用。
        """
        h, w = shape
        fx0, fy0, fx1, fy1 = (int(v) for v in anchors.box)
        face_w = max(1, fx1 - fx0)
        face_h = max(1, fy1 - fy0)
        empty = np.zeros((h, w), dtype=bool)
        if not np.asarray(skin, dtype=bool).any() and not np.asarray(features, dtype=bool).any():
            return (empty, fy1)

        # 搜索范围：脸框左右放宽、上下放宽（YuNet 框通常卡在额头到下巴之间）
        region = np.zeros((h, w), dtype=bool)
        region[
            max(0, int(fy0 - face_h * 0.15)) : min(h, int(fy1 + face_h * 1.30)) + 1,
            max(0, fx0 - int(face_w * 0.55)) : min(w, fx1 + int(face_w * 0.55) + 1),
        ] = True

        seed = (np.asarray(skin, dtype=bool) | np.asarray(features, dtype=bool)) & region
        if not seed.any():
            return (empty, fy1)

        # 闭运算核：刘海宽度约为脸宽的 1/3，取响应尺寸并保证为奇数
        k = max(9, int(round(face_w * 0.32)))
        if k % 2 == 0:
            k += 1
        if HAS_CV2:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            closed = cv2.morphologyEx(
                seed.astype(np.uint8), cv2.MORPH_CLOSE, kernel
            ).astype(bool)
            filled = SemanticSegmenter._fill_holes(closed)
            comps = SemanticSegmenter._components(filled)
            if not comps:
                return (empty, fy1)
            face = comps[0][0]

            # 被刘海切开的额头碎块：与主脸横向重叠、且在主脸纵向范围内
            main_cols = np.where(face.any(axis=0))[0]
            main_area = comps[0][1]
            for comp, area, _cx, _cy in comps[1:]:
                if area < max(20, int(main_area * 0.02)):
                    continue
                rows_i = np.where(comp.any(axis=1))[0]
                cols_i = np.where(comp.any(axis=0))[0]
                if rows_i.size == 0 or cols_i.size == 0:
                    continue
                if cols_i.max() < main_cols.min() or cols_i.min() > main_cols.max():
                    continue
                if rows_i.min() > fy1:
                    continue  # 已经在脸框下方，多为脖子/肩膀
                face |= comp

            # 外沿收紧到「肤色 + 五官」附近，别让闭运算把头发撑进脸里
            near = cv2.dilate(
                seed.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            ).astype(bool)
            face = SemanticSegmenter._fill_holes(face & near)
        else:
            face = SemanticSegmenter._fill_holes(seed)

        # 下颌线：从嘴下方往下扫，行宽骤降到峰值一半处即下颌
        mouth_y = int(round(anchors.mouth_mid[1]))
        span = max(4, int(round(anchors.eye_distance * 2.4)))
        widths = {}
        for y in range(max(0, mouth_y), min(h, mouth_y + span)):
            widths[y] = int(face[y].sum())
        chin_y = fy1
        if widths:
            peak = max(widths.values())
            if peak > 0:
                for y in sorted(widths):
                    if widths[y] < peak * 0.5:
                        chin_y = y
                        break

        face = face & (np.arange(h)[:, None] <= chin_y)
        if HAS_CV2:
            face = cv2.morphologyEx(
                face.astype(np.uint8), cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            ).astype(bool)
            face = SemanticSegmenter._fill_holes(face)
        face &= np.asarray(fg, dtype=bool)
        rows = np.where(face.any(axis=1))[0]
        if rows.size == 0:
            return (empty, fy1)
        return (face, int(rows.max()))

    @staticmethod
    def _skin_reference(
        rgb: np.ndarray,
        skin: np.ndarray,
        region: np.ndarray,
    ) -> np.ndarray:
        """脸内肤色的中位色，作为墨线距离变换的参考色。

        用脸框内的皮肤采样，避免把脖子、手臂的阴影混进来；脸内样本太少
        （冷色角色肤色判据没命中）时退回全图肤色中位数，再不济用中性亮色。
        """
        arr = np.asarray(rgb)
        inner = np.asarray(skin, dtype=bool) & np.asarray(region, dtype=bool)
        if int(inner.sum()) >= 30:
            return np.median(arr[inner], axis=0).astype(np.float32)
        outer = np.asarray(skin, dtype=bool)
        if int(outer.sum()) >= 30:
            return np.median(arr[outer], axis=0).astype(np.float32)
        return np.array([245.0, 215.0, 200.0], dtype=np.float32)

    @staticmethod
    def _ink_mask(rgb: np.ndarray, ref: np.ndarray, threshold: float = 46.0) -> np.ndarray:
        """相对参考肤色明显偏离的像素 = 墨线（睫毛/眼白/瞳孔/嘴线/鼻线）。

        绝对亮度阈值在动漫立绘上不可靠：嘴的描线常被抗锯齿成灰褐色
        （125,108,96），既不够黑也不够红；眼白则接近纯白。改用「离肤色
        中位色多远」一个判据同时覆盖两端。
        """
        arr = np.asarray(rgb, dtype=np.float32)
        delta = arr - np.asarray(ref, dtype=np.float32)
        dist = np.sqrt((delta * delta).sum(axis=2))
        return dist > float(threshold)

    @staticmethod
    def _bounded_runs(
        non_skin: np.ndarray,
        skin_tol: np.ndarray,
        x_lo: int,
        x_hi: int,
        y_lo: int,
        y_hi: int,
        seed_y: float,
        max_height: float,
        margin: int = 2,
    ) -> np.ndarray:
        """按列取「上下都被肤色夹住的非肤色段」，用来切出眼/嘴这类封闭部件。

        为什么按列扫：眼睛和嘴都被皮肤包住（上眼睑/下眼睑、上唇/下唇），
        但它们的**内部**并不是皮肤（眼白、瞳孔、唇线），所以不能靠填洞。
        逐列找包含部件中心的那段非肤色像素，再要求这段的上方与下方
        ``margin`` 像素内出现皮肤——头发是长条状、上方接不到皮肤，会被挡掉。
        """
        non_skin = np.asarray(non_skin, dtype=bool)
        skin_tol = np.asarray(skin_tol, dtype=bool)
        h, w = non_skin.shape
        out = np.zeros((h, w), dtype=bool)
        x_lo, x_hi = max(0, int(x_lo)), min(w - 1, int(x_hi))
        y_lo, y_hi = max(0, int(y_lo)), min(h - 1, int(y_hi))
        if x_hi < x_lo or y_hi < y_lo:
            return out

        for x in range(x_lo, x_hi + 1):
            col = non_skin[:, x]
            rows = np.where(col[y_lo : y_hi + 1])[0]
            if rows.size == 0:
                continue
            seed = int(rows[np.argmin(np.abs(rows + y_lo - seed_y))]) + y_lo
            top = seed
            while top - 1 >= y_lo and col[top - 1]:
                top -= 1
            bot = seed
            while bot + 1 <= y_hi and col[bot + 1]:
                bot += 1
            if (bot - top + 1) > max_height:
                continue
            above = bool(skin_tol[max(0, top - margin) : top, x].any())
            below = bool(skin_tol[bot + 1 : min(h, bot + 1 + margin), x].any())
            if above and below:
                out[top : bot + 1, x] = True
        return out

    @staticmethod
    def _eye_parts(
        anchors,
        skin_tol: np.ndarray,
        fg: np.ndarray,
        shape: Tuple[int, int],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """用 YuNet 双眼关键点切出左右眼（含睫毛/眼白/瞳孔）。

        每只眼的 ROI 以关键点为心、``0.95×双眼间距`` 为半宽，
        再按「双眼中线」切开，避免两侧 ROI 互相抢像素。动漫脸的头身比
        差异很大，用双眼间距而不是脸框宽做尺度，换角色时更稳。
        """
        h, w = shape
        d = float(anchors.eye_distance)
        if not np.isfinite(d) or d < 2.0:
            d = max(2.0, (anchors.box[2] - anchors.box[0]) * 0.45)
        mid_x = (anchors.right_eye[0] + anchors.left_eye[0]) / 2.0
        non_skin = (~np.asarray(skin_tol, dtype=bool)) & np.asarray(fg, dtype=bool)

        eyes: List[np.ndarray] = []
        for name, anchor in (("left", anchors.right_eye), ("right", anchors.left_eye)):
            ax, ay = float(anchor[0]), float(anchor[1])
            x_lo = ax - 0.95 * d
            x_hi = ax + 0.95 * d
            if name == "left":
                x_hi = min(x_hi, mid_x - 1.0)
            else:
                x_lo = max(x_lo, mid_x + 1.0)
            mask = SemanticSegmenter._bounded_runs(
                non_skin,
                skin_tol,
                x_lo,
                x_hi,
                ay - 0.50 * d,
                ay + 0.65 * d,
                ay,
                max_height=0.95 * d,
            )
            mask &= np.asarray(fg, dtype=bool)

            # 眼眶下沿被描线/防晒霜高光断开时，按列结果会碎成几块，
            # 保留该 ROI 内所有成规模的碎块（实测被发丝切开的眼睛会拆成 2 块）。
            comps = SemanticSegmenter._components(mask)
            keep = np.zeros((h, w), dtype=bool)
            min_area = max(6, int(0.0012 * d * d))
            for comp, area, _cx, _cy in comps:
                if area >= min_area:
                    keep |= comp
            eyes.append(keep)

        return eyes[0], eyes[1]

    @staticmethod
    def _eyebrow_parts(
        anchors,
        skin_tol: np.ndarray,
        fg: np.ndarray,
        shape: Tuple[int, int],
    ) -> np.ndarray:
        """眉毛：眼睛上方、被皮肤上下夹住的横向墨线块。

        刘海常常直接盖住眉毛，此时如实返回空掩码（分层清单会记为缺失），
        不用发丝或眼线凑数。判据和眼睛同源：眉毛是小段「上方接皮肤、下方接
        皮肤」的墨线，而刘海是长条（上下接不到皮肤），天然被排除。
        """
        h, w = shape
        out = np.zeros((h, w), dtype=bool)
        d = float(anchors.eye_distance)
        if not np.isfinite(d) or d < 2.0:
            return out
        mid_x = (anchors.right_eye[0] + anchors.left_eye[0]) / 2.0
        non_skin = (~np.asarray(skin_tol, dtype=bool)) & np.asarray(fg, dtype=bool)

        for name, anchor in (("left", anchors.right_eye), ("right", anchors.left_eye)):
            ax, ay = float(anchor[0]), float(anchor[1])
            x_lo = ax - 0.80 * d
            x_hi = ax + 0.80 * d
            if name == "left":
                x_hi = min(x_hi, mid_x - 1.0)
            else:
                x_lo = max(x_lo, mid_x + 1.0)
            out |= SemanticSegmenter._bounded_runs(
                non_skin,
                skin_tol,
                x_lo,
                x_hi,
                ay - 0.78 * d,
                ay - 0.18 * d,
                ay - 0.48 * d,
                max_height=0.32 * d,
            )
        return out & np.asarray(fg, dtype=bool)

    @staticmethod
    def _nose_part(anchors, ink: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
        """鼻子：鼻尖关键点附近的小块墨线（含鼻尖高光）。"""
        h, w = shape
        out = np.zeros((h, w), dtype=bool)
        d = float(anchors.eye_distance)
        if not np.isfinite(d) or d < 2.0:
            return out
        nx, ny = float(anchors.nose[0]), float(anchors.nose[1])
        half_w = max(3, int(round(0.26 * d)))
        y0 = max(0, int(round(ny - 0.12 * d)))
        y1 = min(h - 1, int(round(ny + 0.32 * d)))
        region = np.zeros((h, w), dtype=bool)
        region[y0 : y1 + 1, max(0, int(nx) - half_w) : int(nx) + half_w + 1] = True

        cand = np.asarray(ink, dtype=bool) & region
        for comp, area, _cx, _cy in SemanticSegmenter._components(cand):
            if area >= 2:
                out |= comp
        return out

    @staticmethod
    def _mouth_part(anchors, ink: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
        """嘴：两嘴角关键点连线附近的横向墨线。

        嘴的描线常被抗锯齿拆成几段，所以不要求连成整块，而是逐块按
        「横向（宽 ≥ 高）+ 靠近嘴中线」筛选后合并；竖直块（脸颊线/发丝）
        会被挡掉。
        """
        h, w = shape
        out = np.zeros((h, w), dtype=bool)
        d = float(anchors.eye_distance)
        if not np.isfinite(d) or d < 2.0:
            return out
        rx, ry = float(anchors.right_mouth[0]), float(anchors.right_mouth[1])
        lx, ly = float(anchors.left_mouth[0]), float(anchors.left_mouth[1])
        mx = (rx + lx) / 2.0
        my = (ry + ly) / 2.0
        half_w = max(4, int(round(0.30 * d)))
        y0 = max(0, int(round(my - 0.12 * d)))
        y1 = min(h - 1, int(round(my + 0.38 * d)))
        region = np.zeros((h, w), dtype=bool)
        region[y0 : y1 + 1, max(0, int(mx) - half_w) : int(mx) + half_w + 1] = True

        cand = np.asarray(ink, dtype=bool) & region
        for comp, area, cx, _cy in SemanticSegmenter._components(cand):
            if area < 2:
                continue
            if abs(cx - mx) > 0.30 * d:
                continue
            rows_i = np.where(comp.any(axis=1))[0]
            cols_i = np.where(comp.any(axis=0))[0]
            if rows_i.size == 0 or cols_i.size == 0:
                continue
            comp_h = int(rows_i.max() - rows_i.min() + 1)
            comp_w = int(cols_i.max() - cols_i.min() + 1)
            if comp_w < comp_h:
                continue  # 竖条不是嘴
            out |= comp
        return out

    @staticmethod
    def _despeckle(
        mask: np.ndarray, min_area: int, relative: float = 0.0
    ) -> np.ndarray:
        """去掉孤立碎点，只留下成规模的连通块。

        发丝/描线边缘的抗锯齿会留下大量 1~3 像素的孤立点（实测 hair_back
        被切出 245 个碎点，合计只有 481 像素），拆成图层后它们就是 Live2D 里
        跟着头乱飘的小黑点。结构性图层统一过一遍，五官层不过滤——鼻子本来
        就只有 9 个像素。

        ``relative`` 是按**本部件自身面积**设的相对下限：发丝描边会沿着头发
        轮廓留下一圈几百像素的细条，绝对阈值（几十像素）拦不住，但它们各自
        只占部件面积的千分之几。两部分取大者作为实际阈值。
        """
        mask = np.asarray(mask, dtype=bool)
        if not mask.any() or not HAS_CV2:
            return mask
        threshold = max(int(min_area), int(mask.sum() * relative))
        if threshold <= 1:
            return mask
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), 8
        )
        if count <= 1:
            return mask
        keep = np.zeros(count, dtype=bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= threshold
        return keep[labels]

    @staticmethod
    def _components(mask: np.ndarray) -> List[Tuple[np.ndarray, int, float, float]]:
        """返回 ``(连通块, 面积, 质心x, 质心y)`` 列表，按面积降序。

        没有 cv2 时退化为「整块当作一个连通块」，只用于兜底路径。
        """
        mask = np.asarray(mask, dtype=bool)
        if not mask.any():
            return []
        if not HAS_CV2:
            ys, xs = np.where(mask)
            return [(mask, int(mask.sum()), float(xs.mean()), float(ys.mean()))]
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), 8
        )
        out: List[Tuple[np.ndarray, int, float, float]] = []
        for label in range(1, count):
            comp = labels == label
            out.append(
                (
                    comp,
                    int(stats[label, cv2.CC_STAT_AREA]),
                    float(centroids[label][0]),
                    float(centroids[label][1]),
                )
            )
        out.sort(key=lambda item: item[1], reverse=True)
        return out

    @staticmethod
    def _fill_holes(mask: np.ndarray) -> np.ndarray:
        """把掩码内部的孔洞填实（用于得到「完整的脸」再取五官）。"""
        mask = np.asarray(mask, dtype=bool)
        if not HAS_CV2 or not mask.any():
            return mask
        h, w = mask.shape
        padded = np.zeros((h + 2, w + 2), np.uint8)
        padded[1:-1, 1:-1] = mask.astype(np.uint8)
        ff = np.zeros((h + 4, w + 4), np.uint8)
        inv = np.where(padded == 0, 255, 0).astype(np.uint8)
        cv2.floodFill(inv, ff, (0, 0), 0)
        holes = (inv > 0)[1:-1, 1:-1]
        return mask | holes

    # ----------------------------------------------------------- numpy helpers

    @staticmethod
    def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
        """Vectorized RGB->HSV in pure numpy (returns H:0-180, S:0-255, V:0-255 to mimic cv2)."""
        rgb_f = rgb.astype(np.float32) / 255.0
        r, g, b = rgb_f[..., 0], rgb_f[..., 1], rgb_f[..., 2]
        mx = np.max(rgb_f, axis=-1)
        mn = np.min(rgb_f, axis=-1)
        v = mx
        df = mx - mn
        s = np.where(mx == 0, 0, df / np.maximum(mx, 1e-6))
        # Hue
        rc = (mx - r) / np.maximum(df, 1e-6)
        gc = (mx - g) / np.maximum(df, 1e-6)
        bc = (mx - b) / np.maximum(df, 1e-6)
        h = np.zeros_like(mx)
        h = np.where((mx == r) & (df > 0), (bc - gc), h)
        h = np.where((mx == g) & (df > 0), 2.0 + rc - bc, h)
        h = np.where((mx == b) & (df > 0), 4.0 + gc - rc, h)
        h = (h / 6.0) % 1.0
        # To cv2 scale: H*180 (since H 0-360 in open cv represented as 0-180)
        return np.stack([(h * 180.0).astype(np.uint8),
                         (s * 255.0).astype(np.uint8),
                         (v * 255.0).astype(np.uint8)], axis=-1)

    @staticmethod
    def _largest_component(mask: np.ndarray) -> np.ndarray:
        """Return the largest connected component of a boolean mask."""
        if not mask.any():
            return mask
        if HAS_CV2:
            num, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
            if num <= 1:
                return mask
            # label 0 is background
            sizes = stats[1:, cv2.CC_STAT_AREA]
            largest = int(np.argmax(sizes)) + 1
            return labels == largest
        # Pure-numpy BFS fallback (simple, acceptable for fallback only)
        h, w = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        best_mask = np.zeros_like(mask)
        best_size = 0
        from collections import deque
        ys, xs = np.where(mask & ~visited)
        for y0, x0 in zip(ys.tolist(), xs.tolist()):
            if visited[y0, x0]:
                continue
            dq = deque([(y0, x0)])
            visited[y0, x0] = True
            comp = [(y0, x0)]
            while dq:
                cy, cx = dq.popleft()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            dq.append((ny, nx))
                            comp.append((ny, nx))
            if len(comp) > best_size:
                best_size = len(comp)
                best_mask.fill(False)
                for yy, xx in comp:
                    best_mask[yy, xx] = True
        return best_mask


# Backward-compat alias used by older imports (e.g. semantic.layer_image_file)
SemanticLayerer = SemanticSegmenter


def segment_image_file(path: str, model_type: str = "auto") -> Dict[str, np.ndarray]:
    """Convenience: segment an image file.

    Args:
        path: Path to image.
        model_type: Backend (``"auto"`` picks isnet then fallback).

    Returns:
        Dict of part name -> boolean mask.
    """
    img = Image.open(path).convert("RGBA")
    seg = SemanticSegmenter(model_type="isnet" if model_type == "auto" else model_type)
    return seg.segment(img)


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(description="Live2D Semantic Segmentation")
    parser.add_argument("input", help="Input image path")
    parser.add_argument("--model", default="auto", choices=["auto", "isnet", "sam", "rembg"])
    args = parser.parse_args()
    result = segment_image_file(args.input, model_type=args.model)
    print(f"Segmented {len(result)} parts:")
    for name, m in result.items():
        print(f"  {name:16s} pixels={int(m.sum()):>8d}")
