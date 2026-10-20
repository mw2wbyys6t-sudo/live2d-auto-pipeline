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
        #: 前景掩码来源：onnx_local / rembg / anime_seg / none
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
            parts = self._fallback_color_segment(image)
            fg = masks[0].get("segmentation")
            if fg is not None:
                fg = np.asarray(fg)
                fg = fg > 0 if fg.dtype != bool else fg
                if fg.shape != (h, w):
                    fg = np.array(
                        Image.fromarray(fg.astype(np.uint8) * 255, "L")
                        .resize((w, h), Image.NEAREST)
                    ) > 0
                if fg.any():
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
            "to HSV color heuristics (preview quality only). To enable it, place "
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
        """
        h, w = image.shape[:2]
        union = np.zeros((h, w), dtype=bool)
        for m in masks:
            seg = np.asarray(m.get("segmentation"), dtype=bool)
            if seg.shape == (h, w):
                union |= seg
        box, source = face_detect.detect_face(image, union if union.any() else None)
        self._last_face_source = source
        if box is not None:
            return box
        return (int(w * 0.25), int(h * 0.18), int(w * 0.75), int(h * 0.65))

    # ---------------------------------------------------------- color fallback

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
            fg: 前景布尔掩码（学习式模型产出）。为 None 时退化为按 alpha / 全图。
        """
        rgb = np.array(image.convert("RGB"))
        h, w = rgb.shape[:2]

        # ---------- 前景剪影 ----------
        if fg is not None:
            fg = np.asarray(fg, dtype=bool)
            if fg.shape != (h, w):
                fg = self._resize_mask(fg, image.size)
        elif image.mode == "RGBA":
            fg = np.array(image)[:, :, 3] > 10
        else:
            fg = np.ones((h, w), dtype=bool)
        if not fg.any():
            fg = np.ones((h, w), dtype=bool)

        # ---------- 色彩空间 ----------
        if HAS_CV2:
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        else:
            hsv = self._rgb_to_hsv(rgb)
        sat = hsv[:, :, 1].astype(np.int32)
        val = hsv[:, :, 2].astype(np.int32)

        R = rgb[:, :, 0].astype(np.int32)
        G = rgb[:, :, 1].astype(np.int32)
        B = rgb[:, :, 2].astype(np.int32)
        yy, xx = np.mgrid[0:h, 0:w]

        # 肤色：暖色且明显高于绿蓝；白色衣物 R≈G 会被排除
        skin = fg & (R > 150) & (G > 95) & (B > 75) & (R > G + 8) & (R > B)

        # ---------- 脸框（YuNet 优先，肤色兜底）----------
        box, face_source = face_detect.detect_face(rgb, fg)
        self._last_face_source = face_source
        if box is None:
            fx0, fy0, fx1, fy1 = (
                int(w * 0.38), int(h * 0.18), int(w * 0.62), int(h * 0.42)
            )
        else:
            fx0, fy0, fx1, fy1 = box
        face_h = max(1, fy1 - fy0)
        face_w = max(1, fx1 - fx0)
        face_cx = (fx0 + fx1) / 2.0
        in_face = (xx >= fx0) & (xx <= fx1) & (yy >= fy0) & (yy <= fy1)

        dark = (R < 140) & (G < 140) & (B < 160)

        # ---------- 脸 + 五官 ----------
        # 脸的**肤色**区域（脸底图层）：刘海会把皮肤切成左右两块，靠闭运算
        # 桥接后取最大连通域；同时把下颌线以下的脖子切掉。
        face, chin_y = self._face_skin_region(skin, (fx0, fy0, fx1, fy1))
        if not face.any():
            # 冷色/深色角色：肤色没命中，退化为脸框内的非暗像素
            face = in_face & fg

        # 五官墨线：描线（睫毛/眉/嘴/鼻）+ 眼白。
        # 这里**不能**用「脸的肤色区域 & 非肤色」来找五官：眼睛和嘴在肤色
        # 掩码里是「洞」，但这些洞在拓扑上并不封闭（眼睛左边紧挨着头发），
        # 填洞填不上 → 五官整体丢失。改为在脸框内直接找墨线。
        ink = self._face_ink(val, sat, fg, (fx0, fy0, fx1, fy1))

        # 眼睛所在行：左右两侧都出现非肤色像素的行才可能是眼睛。
        # 取连续峰值段，避免把眉毛那几行误判成眼睛。
        feat_in = features & in_face
        pair = np.minimum(
            (feat_in & (xx < face_cx)).sum(axis=1),
            (feat_in & (xx >= face_cx)).sum(axis=1),
        )
        eye_lo, eye_hi = self._eye_band(fy0, fy1, pair)
        eye_row = (eye_lo + eye_hi) // 2

        brow_lo = max(fy0, eye_lo - int(face_h * 0.28))
        brow_hi = max(fy0, eye_lo)

        # 眼睛：眼带内左右各取最大的一块（眼白 + 瞳孔 + 睫毛连成一体）
        eyes_left = np.zeros((h, w), dtype=bool)
        eyes_right = np.zeros((h, w), dtype=bool)
        eye_band = (yy >= eye_lo) & (yy <= eye_hi) & in_face
        eye_comps = self._components(eye_band & features)
        min_eye = max(12, int(0.0002 * h * w))
        left_pick = None
        right_pick = None
        for comp, area, cx, _cy in eye_comps:
            if area < min_eye:
                continue
            if cx < face_cx:
                if left_pick is None or area > left_pick[1]:
                    left_pick = (comp, area)
            else:
                if right_pick is None or area > right_pick[1]:
                    right_pick = (comp, area)
        if left_pick is not None:
            eyes_left = left_pick[0]
        if right_pick is not None:
            eyes_right = right_pick[0]
        if not eyes_left.any() and not eyes_right.any() and eye_comps:
            # 单个连通块横跨中线：按中线切开
            comp = max(eye_comps, key=lambda c: c[1])[0]
            eyes_left = comp & (xx < face_cx)
            eyes_right = comp & (xx >= face_cx)

        # 眉毛：眼睛上方的横向暗块（竖直的刘海会被形状过滤掉）
        brow_band = (yy >= brow_lo) & (yy <= brow_hi) & in_face
        eyebrows = np.zeros((h, w), dtype=bool)
        for comp, area, cx, cy in self._components(brow_band & dark & fg):
            if area < max(8, int(0.00005 * h * w)):
                continue
            rows_i = np.where(comp.any(axis=1))[0]
            cols_i = np.where(comp.any(axis=0))[0]
            if rows_i.size == 0 or cols_i.size == 0:
                continue
            comp_h = int(rows_i.max() - rows_i.min() + 1)
            comp_w = int(cols_i.max() - cols_i.min() + 1)
            if comp_w < comp_h * 1.5:  # 竖直细条 = 刘海/发丝，不是眉毛
                continue
            eyebrows |= comp

        # ---------- 鼻 / 嘴 ----------
        # 动漫立绘里鼻和嘴都是脸中央的小块，靠「形状 + 相对位置」区分：嘴是
        # 横向的一笔（宽 > 高），鼻是紧凑的一点。绝对亮度阈值在这里不可靠——
        # 嘴的描线常被抗锯齿成灰褐色（127,114,103），既不够黑也不够红，所以
        # 改用「相对皮肤亮度明显偏暗」的判据。
        central = (np.abs(xx - face_cx) <= face_w * 0.32) & in_face
        skin_v = float(np.median(val[face])) if face.any() else 200.0
        darker = val < max(80.0, skin_v * 0.80)
        reddish = (R > 150) & (R > G + 25) & (R > B + 10)
        lower_cand = features & central & (yy > eye_hi) & (dark | reddish | darker)

        # 嘴的描线常被抗锯齿拆成几段，先膨胀再分组，最后取回原始像素
        if HAS_CV2:
            kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            grouped = cv2.dilate(lower_cand.astype(np.uint8), kern).astype(bool)
        else:
            grouped = lower_cand

        min_face_part = max(3, int(0.000005 * h * w))
        picks: List[Tuple[np.ndarray, int, float, float, int, int]] = []
        for comp, _area, _cx, _cy in self._components(grouped):
            original = comp & lower_cand
            area = int(original.sum())
            if area < min_face_part:
                continue
            rows_i = np.where(original.any(axis=1))[0]
            cols_i = np.where(original.any(axis=0))[0]
            picks.append(
                (
                    original,
                    area,
                    float(rows_i.mean()),
                    float(cols_i.mean()),
                    int(cols_i.max() - cols_i.min() + 1),
                    int(rows_i.max() - rows_i.min() + 1),
                )
            )
        picks.sort(key=lambda item: item[2])  # 按纵向位置从上到下

        nose = np.zeros((h, w), dtype=bool)
        mouth = np.zeros((h, w), dtype=bool)
        wide = [p for p in picks if p[4] > p[5]]
        mouth_pick = wide[-1] if wide else (picks[-1] if picks else None)
        if mouth_pick is not None:
            mouth = mouth_pick[0]
        # 鼻：嘴上方、脸正中、紧凑的小块
        nose_picks = [
            p
            for p in picks
            if mouth_pick is None or p[2] < mouth_pick[2]
            if p[5] > 0 and p[4] <= 2 * p[5]
            if abs(p[3] - face_cx) <= face_w * 0.18
        ]
        if nose_picks:
            nose = nose_picks[-1][0]

        # ---------- 头发 ----------
        non_skin = fg & ~skin
        hair = non_skin & (yy < fy0)  # 头顶之上必是头发
        sample = non_skin & (yy < fy0 + int(face_h * 0.5))
        if sample.any():
            # 用发色向下延伸（长发不会停在头顶）
            ref = np.median(rgb[sample], axis=0).astype(np.float32)
            dist = np.sqrt(((rgb.astype(np.float32) - ref) ** 2).sum(axis=2))
            spread = float(np.percentile(dist[sample], 85))
            limit = max(55.0, spread * 1.8)
            near = dist <= limit
            hair = hair | (non_skin & near & (val >= max(40, int(ref.max() * 0.45))))

        hair = hair & ~features
        shoulder_y = fy1 + int(face_h * 0.9)
        if not hair.any() or int(hair.sum()) > int(non_skin.sum()) * 0.85:
            # 发色没区分开（银发/纯黑发等）：退回按位置切
            hair = non_skin & (yy < shoulder_y)
        hair = hair & ~features

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
        neck_zone = (
            (yy > fy1 - int(face_h * 0.05))
            & (yy < fy1 + int(face_h * 0.9))
            & (np.abs(xx - face_cx) <= face_w * 0.6)
        )
        neck = skin & neck_zone & ~face
        if not neck.any():
            neck = skin & (yy > fy1) & (yy < fy1 + face_h) & (np.abs(xx - face_cx) <= face_w)

        # ---------- 衣服 ----------
        clothes = non_skin & ~hair & ~features
        fg_rows = np.where(fg.any(axis=1))[0]
        fg_top = int(fg_rows.min()) if fg_rows.size else 0
        fg_bottom = int(fg_rows.max()) if fg_rows.size else h - 1
        body_h = max(1, fg_bottom - fg_top)
        waist_y = fg_top + int(body_h * 0.45)
        if clothes.any():
            counts = clothes.sum(axis=1)
            if counts.any():
                totals = np.stack(
                    [np.where(clothes, rgb[:, :, c], 0).sum(axis=1) for c in range(3)],
                    axis=1,
                )
                mean_color = totals / np.maximum(counts, 1)[:, None]
                diff = np.abs(np.diff(mean_color, axis=0)).sum(axis=1)
                lo = max(fg_top + int(body_h * 0.35), fy1 + face_h)
                hi = min(fg_top + int(body_h * 0.75), fg_bottom)
                if hi > lo and diff[lo:hi].any():
                    cand = lo + int(np.argmax(diff[lo:hi]))
                    if counts[cand] > 0:
                        waist_y = cand

        clothes_top = clothes & (yy < waist_y)
        clothes_bottom = clothes & (yy >= waist_y)

        # ---------- 四肢 ----------
        skin_body = skin & ~face & ~neck
        arms = np.zeros((h, w), dtype=bool)
        hands = np.zeros((h, w), dtype=bool)
        legs = np.zeros((h, w), dtype=bool)
        leg_top = fg_bottom
        for comp, _area, _cx, _cy in self._components(skin_body):
            rows = np.where(comp.any(axis=1))[0]
            top = int(rows.min())
            if top < waist_y:
                # 手臂：末端一小段是手
                bottom = int(rows.max())
                cut = bottom - max(1, int((bottom - top + 1) * 0.15))
                arms |= comp & (yy < cut)
                hands |= comp & (yy >= cut)
            else:
                legs |= comp
                leg_top = min(leg_top, top)

        # 腿以下的非肤色（袜子/鞋）单独成层，不混进裙子
        legwear = clothes_bottom & (yy >= leg_top)
        clothes_bottom = clothes_bottom & (yy < leg_top)

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
            "accessories": np.zeros((h, w), dtype=bool),
        }
        return {k: v for k, v in out.items() if v.any()}

    # ------------------------------------------------------- numpy 辅助（掩码）

    @staticmethod
    def _face_skin_region(
        skin: np.ndarray,
        box: Tuple[int, int, int, int],
    ) -> np.ndarray:
        """取出脸的皮肤区域（含五官位置的完整脸形）。

        关键细节一：**穿过脸的刘海会把皮肤切成左右两块**，直接取最大连通域
        只会拿到半张脸（实测脸只剩 0.7%、眼睛完全丢失）。所以先在脸框邻域
        内做形态学闭运算把刘海连起来——但闭运算只用来**定位**脸部连通域。

        关键细节二：闭运算会把边缘膨胀出去，顺带把下颌线和脖子一起吞进来
        （实测脖子被算成脸、嘴的位置被下颌线抢走）。所以最终只保留**原始
        肤色像素**，并把位于脸框下部的脖子那一块剔除。
        """
        mask = np.asarray(skin, dtype=bool)
        if not mask.any():
            return mask

        h, w = mask.shape
        fx0, fy0, fx1, fy1 = (int(v) for v in box)
        pad = int(max(1, fx1 - fx0) * 0.25)
        region = np.zeros((h, w), dtype=bool)
        region[
            max(0, fy0 - pad) : fy1 + pad + 1,
            max(0, fx0 - pad) : fx1 + pad + 1,
        ] = True
        near = mask & region
        if not near.any():
            return near

        # 闭运算：把宽度小于核尺寸的刘海桥接起来（仅用于定位）
        if HAS_CV2:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
            closed = cv2.morphologyEx(
                near.astype(np.uint8), cv2.MORPH_CLOSE, kernel
            ).astype(bool)
        else:
            closed = near

        blob = SemanticSegmenter._largest_component(closed)
        raw = blob & mask  # 原始肤色像素：下颌线与脖子不会再被算进脸

        # 脸 vs 脖子：脖子在脸框下部，且与脸部的纵向重叠很小
        comps = sorted(
            SemanticSegmenter._components(raw), key=lambda item: item[1], reverse=True
        )
        if not comps:
            return SemanticSegmenter._fill_holes(near)
        main = comps[0][0]
        main_rows = np.where(main.any(axis=1))[0]
        main_lo, main_hi = int(main_rows.min()), int(main_rows.max())
        face = main.copy()
        for comp, _area, _cx, _cy in comps[1:]:
            rows_i = np.where(comp.any(axis=1))[0]
            lo, hi = int(rows_i.min()), int(rows_i.max())
            overlap = min(hi, main_hi) - max(lo, main_lo) + 1
            if overlap >= (hi - lo + 1) * 0.5:
                face |= comp  # 被刘海切开的另一半脸

        face = SemanticSegmenter._fill_holes(face)
        # 脖子在脸框底部以下，切掉
        face = face & (np.arange(h)[:, None] <= fy1)
        return face

    @staticmethod
    def _eye_band(fy0: int, fy1: int, pair: np.ndarray) -> Tuple[int, int]:
        """从左右对称性剖面里找出眼睛所在行区间。

        ``pair[y]`` 是第 y 行左半边与右半边「非肤色像素数」的较小值：只有
        左右同时有内容（两只眼睛）时才会大。取连续峰值段，避免把眉毛那几行
        误当成眼睛。
        """
        band = np.asarray(pair)[fy0 : fy1 + 1]
        if band.size == 0 or band.max() <= 0:
            return fy0, fy1
        threshold = max(2, int(float(band.max()) * 0.45))
        rows = np.where(band >= threshold)[0]
        if rows.size == 0:
            return fy0, fy1
        groups = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
        best = max(groups, key=lambda g: (int(band[g].sum()), len(g)))
        return int(best[0]) + fy0, int(best[-1]) + fy0

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
