#!/usr/bin/env python3
"""
Live2D Master Agent - Layer Composition & Ordering

Given per-part boolean masks and the original image, this module extracts
transparent RGBA layers, applies amodal completion for occluded regions,
saves each layer as a PNG, reorders them to the standard Live2D draw order,
emits layer metadata JSON, and builds an occlusion map.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from core.logger import get_logger
from core.segment_engine.amodal import AmodalCompleter
from core.segment_engine.part_split import (
    attach_depths,
    split_depth_parts,
    split_eye_parts,
    split_garment_details,
    split_hair_parts,
    split_limb_parts,
)

log = get_logger("segment.composer")


class LayerComposer:
    """Compose per-part masks into ordered, exported Live2D layers.

    The standard draw order (back-to-front) follows conventional Live2D
    Cubism rigging: scalp/hair behind, then face features, clothing, then
    foreground hair and effects.
    """

    STANDARD_LAYER_ORDER: List[str] = [
        "scalp",
        "hair_back",
        "hair_mid",
        # 刘海（hair_front / hair_bangs）**不在这里**：见下方眼睑之后的位置。
        # 原因：脸层在刘海位置是镂空的（分出来的脸只有可见皮肤），所以"刘海"原本
        # 是靠脸的洞透出来的。闭眼图一旦给那些洞填色，刘海就被涂成皮肤（实测
        # 1206 px）。把刘海排到脸/闭眼图之后，它直接盖在上面，与 `preview.png` 的
        # 逐像素误差反而更小（MAE 1.5767 → 1.5695）。
        # 脸底层必须在**五官之前**（列表越靠后 = 画得越靠前）。
        #
        # 历史顺序把 face_base 排在 eyebrows/eyes 之后：那时 face 层里复制了眼睛
        # 像素，谁在前都看不出来。脸层被清成纯皮肤后（见 FACE_PARTS），顺序立刻
        # 显形——实测眼睛被脸盖成几块碎片。
        "face_base",
        "face",
        # 腮红：合成层，画在脸皮上、五官之下。静止时**不可见**（不透明度 0），
        # 由 ParamCheek 淡入 —— 见 REST_INVISIBLE_PARTS。
        "cheek",
        # 闭眼图：合成层（见 synthesize_face_geometry）。必须排在眼球**之前**
        # （列表越靠后 = 画得越靠前）—— 闭眼图是"眼皮落下后露出来的那张脸"，
        # 画在眼球下面，眼球向上收拢时才露出来。
        "eyelid_left", "eyelid_right",
        # 五官：真实分层器产出的名字（eyewhite_* / irides_* / eyebrow_*）都要列上，
        # 否则它们会落到"未列出→按字母序追加到最后"，被画到头发和衣服之上。
        "eyes", "eyes_left", "eyes_right",
        "eyewhite_left", "eyewhite_right",
        "irides_left", "irides_right",
        "eyelash_left", "eyelash_right",
        # 眼泪：合成层，画在眼睛之上。静止时**不可见**（不透明度 0），
        # 由 ParamTears × ParamTearsL/R 淡入。
        "tear_left", "tear_right",
        # 刘海：必须排在闭眼图之后（画在闭眼图前），否则闭眼图的补色会盖住刘海。
        "hair_front", "hair_bangs",
        "eyebrows", "eyebrow_L", "eyebrow_R",
        "nose", "nose_mouth",
        # 口内：合成层，必须在嘴线**之前**（先画腔体，再画嘴唇）。
        "mouth_cavity", "mouth",
        "neck",
        "clothes_top",
        "clothes_inner",
        "waistband",
        "collar",
        "sleeve_L",
        "sleeve_R",
        "cuff_L",
        "cuff_R",
        "arms",
        "arm_L",
        "arm_R",
        "hands",
        "hand_L",
        "hand_R",
        "skirt",
        "skirt_back",
        "bottomwear_back",
        "clothes_bottom",
        "skirt_front",
        "bottomwear_front",
        "skirt_hem_L",
        "skirt_hem_R",
        "skirt_hem",
        "legs",
        "leg_L",
        "leg_R",
        "legwear",
        "sock_L",
        "sock_R",
        "boot_L",
        "boot_R",
        "boot_cuff_L",
        "boot_cuff_R",
        "accessories",
        "tail_ears",
        "effects",
    ]

    # Mask names that should receive amodal completion (regions typically
    # occluded by other parts in front of them).
    AMODAL_PARTS = {"hair_back", "hair_mid", "clothes_top", "clothes_inner", "neck"}

    #: 脸底层。它必须是**干净皮肤**——下面的五官由各自的网格驱动（眨眼、说话），
    #: 脸层里若留着同样的五官像素，那些参数就会"改了但看不出来"。
    #: 实测：`face.png` 在 (308,155) 的像素与 `irides_left.png` 完全相同，
    #: 于是 ParamEyeLOpen=0 时眼睛确实被压扁了，脸层又把瞳孔原样露出来。
    FACE_PARTS = ("face", "face_base")

    #: 画在脸上的五官层。导出脸底层时这些区域的像素要被肤色补掉。
    FACE_FEATURE_PARTS = (
        "eyes_left", "eyes_right", "eyebrows", "nose", "mouth",
        "eyewhite_left", "eyewhite_right", "irides_left", "irides_right",
        "eyelash_left", "eyelash_right", "eye_left", "eye_right",
        "eye_iris_left", "eye_iris_right", "eye_white_left", "eye_white_right",
    )

    #: 补掉五官时向外扩的像素数：五官边缘有 1~2px 抗锯齿，不扩会留一圈深色描边，
    #: 眨眼/闭嘴时那一圈会露出来，看起来像"眼睛还在"。
    FEATURE_ERASE_MARGIN = 2

    #: 合成上眼睑：层名 -> 可以作为"眼睛可见区域"的候选层（按优先级）。
    #:
    #: 立绘里没有眼皮画稿，闭眼只能把眼球压扁——眼睛变成一坨被压扁的色块。这里合成
    #: 一张「闭眼图」：眼睛轮廓内填皮肤 + 原画自己的**上睫毛线**。
    #:
    #: 为什么不用"眼皮带下移"：带子要从眼睛上方取像素，而眼睛正上方整片是头发，
    #: 脸层在那儿存的是**发色**（不是皮肤）——照抄下来闭眼时眼睛上会留一条蓝带
    #: （实测左眼 40px 宽）。所以改成"闭眼图"：形状与颜色都在眼睛**内部**取，
    #: 绝不借用眼睛外的像素。
    EYELID_SOURCES = (
        ("eyelid_left",
         ("irides_left", "eyewhite_left", "eyes_left", "eye_left")),
        ("eyelid_right",
         ("irides_right", "eyewhite_right", "eyes_right", "eye_right")),
    )
    #: 睫毛线：眼球层里"暗且各通道接近"的像素（描边/睫毛），虹膜是蓝的会被排除。
    EYELID_LASH_LUM = 110
    EYELID_LASH_SPREAD = 30

    #: 腮红层：脸的颊部，由脸层原像素向暖粉偏。
    CHEEK_LAYER = "cheek"
    #: 腮红相对眼睛尺寸的几何。
    #:
    #: 必须**向外**偏：动漫脸两只眼睛本来就靠得近，若把椭圆正对眼睛中心放、
    #: 半径又开到一整个眼宽，两块会连成一条横跨鼻梁的硬边色带（实测 86x30 的
    #: 一条带子）。所以中心外移、半径收小，左右两块各自成团。
    CHEEK_RADIUS_X = 0.58
    CHEEK_RADIUS_Y = 0.42
    CHEEK_OUTWARD = 0.45
    CHEEK_CENTER_DY = 0.55
    #: 腮红最大不透明度（0..255）。半透明才像腮红，实心会像贴了两块色块。
    CHEEK_ALPHA = 120
    #: 腮红颜色 = 肤色 + 这个偏移（R 抬、G/B 压 -> 暖粉）。
    CHEEK_TINT = (18, -16, -12)

    #: 眼泪层：左右各一，画在眼睛下缘。静止时**不可见**，由 ParamTears × 左右淡入。
    #:
    #: 只做"下眼睑蓄泪"这一条薄新月：泪珠之类的圆点在小画布上会被网格采样成
    #: 方块（实测），反而比没有更难看。
    TEAR_SIDES = (("tear_left", 0), ("tear_right", 1))
    TEAR_RADIUS_X = 0.40
    TEAR_RADIUS_Y = 0.20
    TEAR_CENTER_DY = 0.02
    TEAR_ALPHA = 150
    TEAR_RGB = (196, 226, 255)

    #: 静止时**不可见**的合成层（靠不透明度键形淡入）。
    #:
    #: 这三层与闭眼图不同：闭眼图静止时被眼球完全挡住，而腮红/眼泪静止时必须靠
    #: **不透明度 0** 隐去。所以合成预览要跳过它们（否则静止画面凭空多出腮红），
    #: 质量门也不能把它们当成"越界扩张"（它们静止时根本不画）。
    REST_INVISIBLE_PARTS = (CHEEK_LAYER, "tear_left", "tear_right")

    #: 口内层：从嘴线层取形状与色调，合成"口腔内侧"。
    MOUTH_CAVITY_SOURCES = ("mouth", "mouth_outer", "mouth_line")
    MOUTH_CAVITY_NAME = "mouth_cavity"
    #: 口内色 = 嘴线色向暖红偏移的比例（嘴线通常是近黑的墨线）。
    MOUTH_CAVITY_WARMTH = 0.45

    @classmethod
    def synthesized_overlay_sources(cls) -> Dict[str, Tuple[str, ...]]:
        """合成层 → 它**取自**哪些层。

        闭眼图与口内都是拿源层的**同一块面积**造出来的（闭眼图 = 眼睛那块面积填
        皮肤 + 原画上睫毛；口内 = 嘴线那块面积填暖色），所以它们必然与源层
        100% 重叠。质量门按"谁盖住谁"判缺陷，不认识这条关系就会把设计报成越界
        扩张（实测 4 条：eyelid_* 各盖住 eyewhite_* 与 irides_*）。
        """
        out: Dict[str, Tuple[str, ...]] = {
            name: tuple(sources) for name, sources in cls.EYELID_SOURCES
        }
        out[cls.MOUTH_CAVITY_NAME] = tuple(cls.MOUTH_CAVITY_SOURCES)
        return out

    def __init__(self, device: str = "auto") -> None:
        """Initialize the composer.

        Args:
            device: Compute device forwarded to :class:`AmodalCompleter`.
        """
        self.amodal = AmodalCompleter(device=device)

    # ------------------------------------------------------------------ public

    def compose(
        self,
        part_masks: Dict[str, np.ndarray],
        original_image: Image.Image,
        output_dir: str,
        depth_map: Optional[np.ndarray] = None,
    ) -> Dict[str, Dict]:
        """Extract, complete, and save per-part RGBA layers.

        Args:
            part_masks: Mapping of part name -> boolean mask ``(H, W)``.
            original_image: Source character image (RGBA or convertible).
            output_dir: Directory in which to write ``<part>.png`` files.

        Returns:
            Dict keyed by part name with metadata:
            ``path``, ``bbox``, ``size``, ``pixel_count``, ``mean_color``,
            ``occluded_by``, ``completed``.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        if original_image.mode != "RGBA":
            original_image = original_image.convert("RGBA")
        img_arr = np.array(original_image)
        h, w = img_arr.shape[:2]

        part_masks, split_warnings = split_limb_parts(part_masks, (w, h))
        part_masks, eye_warnings = split_eye_parts(part_masks, img_arr)
        part_masks, hair_warnings = split_hair_parts(part_masks)
        part_masks, derived_parts = split_garment_details(part_masks, (w, h))
        depth_warnings: List[str] = []
        if depth_map is not None:
            part_masks, depth_warnings = split_depth_parts(part_masks, depth_map)
        for message in split_warnings + eye_warnings + hair_warnings + depth_warnings:
            log.warning(message)
        if derived_parts:
            log.info(f"Derived garment layers: {derived_parts}")

        occlusion_map = self.generate_occlusion_map(part_masks)

        # 脸底层要"掏掉"的五官区域（见 FACE_PARTS 的说明）。先并起来再统一补。
        face_features = np.zeros((h, w), dtype=bool)
        for name in self.FACE_FEATURE_PARTS:
            m = part_masks.get(name)
            if m is not None:
                face_features |= np.asarray(m, dtype=bool)
        if face_features.any() and self.FEATURE_ERASE_MARGIN > 0:
            face_features = self._dilate(face_features, self.FEATURE_ERASE_MARGIN)

        layers: Dict[str, Dict] = {}
        for part, mask in part_masks.items():
            mask = np.asarray(mask, dtype=bool)
            if mask.shape != (h, w):
                log.warning(f"Skipping part '{part}': mask shape {mask.shape} != image {(h, w)}")
                continue
            if not mask.any():
                log.debug(f"Skipping empty part '{part}'")
                continue

            # Determine occluded pixels for this part: pixels that belong to
            # a part drawn in front of this one.
            occluded_by = occlusion_map.get(part, {}).get("occluded_by", [])
            occluded_mask = np.zeros((h, w), dtype=bool)
            for other in occluded_by:
                if other in part_masks:
                    occluded_mask |= part_masks[other].astype(bool)
            occluded_mask &= ~mask  # only pixels we don't already own

            completed = False
            if part in self.AMODAL_PARTS and occluded_mask.any():
                try:
                    completed_layer = self.amodal.complete(
                        original_image,
                        visible_mask=mask,
                        occluded_mask=occluded_mask,
                    )
                    completed_arr = np.array(completed_layer)
                    # Preserve RGB from the original on visible pixels, fill
                    # alpha as union(mask, occluded_mask)
                    layer_arr = np.zeros((h, w, 4), dtype=np.uint8)
                    union = mask | occluded_mask
                    layer_arr[union, :3] = completed_arr[union, :3]
                    layer_arr[union, 3] = 255
                    # But we must NOT steal visible pixels that are another
                    # part's (those stay transparent on this layer).
                    other_parts = np.zeros((h, w), dtype=bool)
                    for name, other_mask in part_masks.items():
                        if name == part:
                            continue
                        other_parts |= np.asarray(other_mask, dtype=bool)
                    # Visible pixels of OTHER parts are transparent here
                    # (except our own visible pixels)
                    other_visible = other_parts & ~mask
                    layer_arr[other_visible, 3] = 0
                    completed = True
                except Exception as exc:
                    log.warning(f"Amodal completion failed for '{part}': {exc}")
                    layer_arr = self._extract_layer(img_arr, mask)
            else:
                layer_arr = self._extract_layer(img_arr, mask)

            if part in self.FACE_PARTS and face_features.any():
                layer_arr = self._erase_features(layer_arr, mask, face_features)

            path = out / f"{part}.png"
            Image.fromarray(layer_arr, "RGBA").save(path)

            pixels = layer_arr[..., 3] > 0
            ys, xs = np.where(pixels)
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else (0, 0, 0, 0)
            if pixels.any():
                mean_color = tuple(int(c) for c in layer_arr[pixels][:, :3].mean(axis=0))
            else:
                mean_color = (0, 0, 0)

            layers[part] = {
                "name": part,
                "path": str(path),
                "bbox": bbox,
                "size": (int(bbox[2] - bbox[0] + 1), int(bbox[3] - bbox[1] + 1)),
                "pixel_count": int(pixels.sum()),
                "mean_color": mean_color,
                "occluded_by": list(occluded_by),
                "completed": completed,
            }
            log.debug(
                f"  layer {part:14s} pixels={layers[part]['pixel_count']:>7d} "
                f"bbox={bbox} completed={completed}"
            )

        synthesized = self.synthesize_face_geometry(layers, h, w, out)
        if synthesized:
            log.info(f"合成表情图层（眼睑/口内）：{synthesized}")

        if depth_map is not None:
            attach_depths(layers, part_masks, depth_map)

        log.success(f"Composed {len(layers)} layers into {out}")
        return layers

    def synthesize_face_geometry(
        self,
        layers: Dict[str, Dict],
        h: int,
        w: int,
        out: Optional[Path] = None,
    ) -> List[str]:
        """合成「闭眼图」「口内」「腮红」「眼泪」四类层，返回新增的层名。

        立绘里没有这些画稿：眼睛是睁开的、嘴是闭着的、没有腮红和眼泪。缺了它们，
        眨眼只能把眼球压扁，张嘴只能把墨线撑粗，`ParamCheek` / `ParamTears*` 更是
        改了也毫无反应。

        两种截然不同的"静止不可见"策略：
        * **被挡住**：闭眼图与口内靠更靠前的图层遮住（静止逐像素不变）。
        * **不透明度 0**：腮红与眼泪静止时是透明的（``rest_opacity=0``），由参数
          键形在运行时淡入。它们不参与"静止逐像素不变"的证明，而是靠
          ``REST_INVISIBLE_PARTS`` 与预览端跳过。

        合成失败（缺脸层/缺眼睛层/层为空）一律**不产出**，不交半层。
        """
        made: List[str] = []
        if not layers:
            return made
        dest = Path(out) if out is not None else Path(
            next(iter(layers.values()))["path"]).parent
        self._array_cache: Dict[str, Optional[np.ndarray]] = {}
        try:
            return self._synthesize_layers(layers, h, w, dest, made)
        finally:
            self._array_cache = None

    def _synthesize_layers(self, layers: Dict[str, Dict], h: int, w: int,
                           dest: Path, made: List[str]) -> List[str]:
        for name, sources in self.EYELID_SOURCES:
            if name in layers:
                continue
            present = self._draw_order([s for s in sources if s in layers])
            if not present:
                continue
            lid = self._synthesize_eyelid(name, present, layers, h, w)
            if lid is None:
                log.debug(f"跳过眼睑合成 '{name}': 眼睛层不可用")
                continue
            self._store_layer(layers, dest, name, lid)
            made.append(name)

        if self.MOUTH_CAVITY_NAME not in layers:
            present = self._draw_order(
                [s for s in self.MOUTH_CAVITY_SOURCES if s in layers])
            if present:
                cavity = self._synthesize_mouth_cavity(layers, present, h, w)
                if cavity is not None:
                    self._store_layer(layers, dest, self.MOUTH_CAVITY_NAME, cavity)
                    made.append(self.MOUTH_CAVITY_NAME)

        # ---- 静止时不可见的两类表情层（靠不透明度键形淡入）----
        # 它们**不**参与"静止逐像素不变"的证明：静止时由不透明度 0 隐去，
        # 所以 `rest_opacity=0` 必须一路带到合成预览与导出端。
        if self.CHEEK_LAYER not in layers:
            cheek = self._synthesize_cheek(layers, h, w)
            if cheek is not None:
                self._store_layer(layers, dest, self.CHEEK_LAYER, cheek,
                                  rest_opacity=0.0)
                made.append(self.CHEEK_LAYER)

        for name, index in self.TEAR_SIDES:
            if name in layers:
                continue
            tear = self._synthesize_tear(layers, h, w, index)
            if tear is not None:
                self._store_layer(layers, dest, name, tear, rest_opacity=0.0)
                made.append(name)
        return made

    def _eye_anchor(self, layers: Dict[str, Dict], h: int, w: int,
                    index: int) -> Optional[tuple]:
        """该侧眼睛**不透明**像素的包围盒 ``(x0, y0, x1, y1)``；没有眼睛层则 None。"""
        _name, sources = self.EYELID_SOURCES[index]
        eye = self._alpha_over([self._layer_array(layers, n) for n in sources], h, w)
        if eye is None:
            return None
        solid = eye[..., 3] == 255
        if not solid.any():
            return None
        ys, xs = np.nonzero(solid)
        return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))

    def _skin_array(self, layers: Dict[str, Dict]) -> Optional[np.ndarray]:
        face = self._layer_array(layers, "face")
        if face is None:
            face = self._layer_array(layers, "face_base")
        return face

    @staticmethod
    def _radial_alpha(h: int, w: int, cx: float, cy: float,
                      rx: float, ry: float, peak: float) -> np.ndarray:
        """椭圆径向衰减的 alpha（0..peak），边缘平滑到 0。"""
        if rx <= 0 or ry <= 0:
            return np.zeros((h, w), dtype=np.float64)
        ys = np.arange(h, dtype=np.float64)[:, None]
        xs = np.arange(w, dtype=np.float64)[None, :]
        norm = ((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2
        falloff = np.clip(1.0 - norm, 0.0, 1.0) ** 1.5
        return falloff * float(peak)

    def _synthesize_cheek(self, layers: Dict[str, Dict], h: int,
                          w: int) -> Optional[np.ndarray]:
        """合成腮红层：颊部两块椭圆，颜色取自脸层肤色向暖粉偏。

        静止时**不可见**（``rest_opacity=0``，由 ``ParamCheek`` 淡入），所以它不需要
        像闭眼图那样"藏在别的层后面"——但也不能是凭空画的色块：颜色来自脸层
        自己的像素，只做色偏，这样与皮肤的明暗关系保持一致。
        """
        face = self._skin_array(layers)
        if face is None:
            return None
        anchors = [(i, a) for i, a in
                   ((i, self._eye_anchor(layers, h, w, i))
                    for i in range(len(self.EYELID_SOURCES))) if a]
        if not anchors:
            return None

        skin = self._skin_color_below(face, max(a[3] for _i, a in anchors))
        if skin is None:
            return None
        tint = np.asarray(self.CHEEK_TINT, dtype=np.int16)
        colour = np.clip(skin.astype(np.int16) + tint, 0, 255).astype(np.uint8)

        alpha = np.zeros((h, w), dtype=np.float64)
        for index, (x0, y0, x1, y1) in anchors:
            eye_w = max(1.0, float(x1 - x0))
            eye_h = max(1.0, float(y1 - y0))
            # 向外偏（左眼往左、右眼往右），两团不连成一条横带
            sign = -1.0 if index == 0 else 1.0
            if len(anchors) == 1:
                sign = 0.0
            alpha = np.maximum(alpha, self._radial_alpha(
                h, w,
                cx=(x0 + x1) / 2.0 + sign * self.CHEEK_OUTWARD * eye_w,
                cy=y1 + self.CHEEK_CENTER_DY * eye_h,
                rx=self.CHEEK_RADIUS_X * eye_w,
                ry=self.CHEEK_RADIUS_Y * eye_h,
                peak=self.CHEEK_ALPHA))

        # 只画在脸的**实际皮肤**上：脸层在刘海/五官处是镂空的，越界会糊到头发上。
        on_face = face[..., 3] > 0
        alpha = np.where(on_face, alpha, 0.0)
        if not (alpha > 0).any():
            return None

        cheek = np.zeros((h, w, 4), dtype=np.uint8)
        cheek[..., :3] = colour
        cheek[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
        return cheek

    def _synthesize_tear(self, layers: Dict[str, Dict], h: int, w: int,
                         index: int) -> Optional[np.ndarray]:
        """合成单侧眼泪层：眼睛下缘的一小片高光，外加一颗泪珠。

        静止时**不可见**（``rest_opacity=0``，由 ``ParamTears × ParamTearsL/R``
        淡入）。画在眼睛**之上**（见 ``STANDARD_LAYER_ORDER``），所以只覆盖下眼睑
        附近，不去遮瞳孔。
        """
        anchor = self._eye_anchor(layers, h, w, index)
        if anchor is None:
            return None
        face = self._skin_array(layers)
        x0, y0, x1, y1 = anchor
        eye_w = max(1.0, float(x1 - x0))
        eye_h = max(1.0, float(y1 - y0))
        cx = (x0 + x1) / 2.0
        cy = y1 + self.TEAR_CENTER_DY * eye_h

        alpha = self._radial_alpha(
            h, w, cx=cx, cy=cy,
            rx=self.TEAR_RADIUS_X * eye_w,
            ry=self.TEAR_RADIUS_Y * eye_h,
            peak=self.TEAR_ALPHA)

        if face is not None:
            # 眼泪可以压在眼球上，但不能糊到脸外的头发/背景上
            visible = face[..., 3] > 0
            eye_own = self._alpha_over(
                [self._layer_array(layers, n)
                 for n in self.EYELID_SOURCES[index][1]], h, w)
            if eye_own is not None:
                visible = visible | (eye_own[..., 3] > 0)
            alpha = np.where(visible, alpha, 0.0)
        if not (alpha > 0).any():
            return None

        tear = np.zeros((h, w, 4), dtype=np.uint8)
        tear[..., :3] = np.asarray(self.TEAR_RGB, dtype=np.uint8)
        tear[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
        return tear

    def _synthesize_eyelid(self, name: str, sources: List[str],
                           layers: Dict[str, Dict], h: int,
                           w: int) -> Optional[np.ndarray]:
        """合成「闭眼图」，画在眼球**下面**（见 ``STANDARD_LAYER_ORDER``）。

        闭眼时眼球整体**向上收拢**进上睫毛里（``eye`` profile，轴心取眼睛轮廓上沿），
        收拢后露出来的就是这张图。

        闭眼图 = 眼睛轮廓内填皮肤 + **原画自己的上睫毛线**（暗、且各通道接近的
        像素；虹膜是蓝的，各通道差大，会被排除）。

        为什么不能"从眼睛上方搬一条眼皮带下来"：眼睛正上方整片是头发，脸层在那儿
        存的是**发色**（原图像素），照抄下来闭眼时眼睛上会留一条 40px 宽的蓝带
        （实测）。闭眼图的像素全部取自眼睛**内部**，不借用眼睛外的像素。

        静止姿态逐像素不变：
        * 皮肤部分 = 脸层自己的像素（该区域已被 inpaint 成肤色）；
        * 睫毛部分 = 眼球层自己的像素，叠在眼球层上完全一样。
        """
        eye = self._alpha_over([self._layer_array(layers, n) for n in sources], h, w)
        if eye is None:
            return None
        solid = eye[..., 3] == 255
        if not solid.any():
            return None

        face = self._layer_array(layers, "face")
        if face is None:
            face = self._layer_array(layers, "face_base")
        if face is None:
            return None

        ys, xs = np.nonzero(solid)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())

        # 上睫毛线：眼球层里暗、且各通道接近的像素。只取眼睛**上半部** ——
        # 下半部的深色是下睫毛/眼眶线，闭眼时不该留在原位。
        rgb = eye[..., :3].astype(np.int16)
        lum = rgb.mean(axis=2)
        spread = rgb.max(axis=2) - rgb.min(axis=2)
        dark = solid & (lum < self.EYELID_LASH_LUM) & (spread < self.EYELID_LASH_SPREAD)
        upper = np.zeros_like(dark)
        upper[y0:(y0 + y1) // 2 + 1, :] = True
        lash = dark & upper
        if not lash.any():
            return None

        # 皮肤：脸层在眼区已被 inpaint 成肤色，直接搬过来；脸层透明的地方
        # （眼区在脸层是有洞的）用眼睛下方脸颊的肤色补上。
        skin = self._skin_color_below(face, y1)
        if skin is None:
            return None
        region_face = face[y0:y1 + 1, x0:x1 + 1]
        body = region_face[..., :3].astype(np.int16).copy()
        hole = region_face[..., 3] != 255
        body[hole] = skin.astype(np.int16)

        lid = np.zeros((h, w, 4), dtype=np.uint8)
        region = lid[y0:y1 + 1, x0:x1 + 1]
        region[..., :3] = np.clip(body, 0, 255).astype(np.uint8)
        region[..., 3] = np.where(solid[y0:y1 + 1, x0:x1 + 1], 255, 0)
        # 睫毛线：原样保留眼球层的像素（静止时与眼球层逐字节相同）。
        local_lash = lash[y0:y1 + 1, x0:x1 + 1]
        region[local_lash] = eye[y0:y1 + 1, x0:x1 + 1][local_lash]
        return lid

    def _skin_color_below(self, face: Optional[np.ndarray], eye_bottom: int
                           ) -> Optional[np.ndarray]:
        """眼睛下方脸颊的肤色中位数（闭眼图里补脸层破洞用）。"""
        if face is None:
            return None
        below = face[eye_bottom + 1:, :, :3]
        alpha = face[eye_bottom + 1:, :, 3]
        keep = alpha == 255
        if not keep.any():
            return None
        rgb = below[keep].astype(np.int16)
        lum = rgb.mean(axis=1)
        spread = rgb.max(axis=1) - rgb.min(axis=1)
        skinish = (lum >= 140) & (spread <= 90)
        pool = rgb[skinish] if skinish.any() else rgb
        return np.median(pool, axis=0).astype(np.uint8)

    def _synthesize_mouth_cavity(self, layers: Dict[str, Dict],
                                 sources: List[str], h: int,
                                 w: int) -> Optional[np.ndarray]:
        """按嘴线形状生成口内层：轮廓与嘴唇**完全重合**，静止时不可见。"""
        mouth = self._alpha_over([self._layer_array(layers, n) for n in sources], h, w)
        if mouth is None:
            return None
        solid = mouth[..., 3] >= 250
        if not solid.any():
            return None
        line = np.median(mouth[solid][:, :3], axis=0).astype(np.float32)
        # 墨线多为近黑：往暖红抬，得到"口腔内侧"而不是第二条黑线。
        warm = np.array([140.0, 56.0, 56.0], dtype=np.float32)
        ratio = min(1.0, max(0.0, float(self.MOUTH_CAVITY_WARMTH)))
        colour = np.clip(line * (1.0 - ratio) + warm * ratio, 0, 255).astype(np.uint8)
        cavity = np.zeros((h, w, 4), dtype=np.uint8)
        cavity[solid, :3] = colour
        cavity[solid, 3] = 255
        return cavity

    def _draw_order(self, names: List[str]) -> List[str]:
        """按 ``STANDARD_LAYER_ORDER`` 排序（越靠后 = 越靠前）。"""
        rank = {n: i for i, n in enumerate(self.STANDARD_LAYER_ORDER)}
        return sorted(names, key=lambda n: rank.get(n, len(rank)))

    def _alpha_over(self, stack, h: int, w: int) -> Optional[np.ndarray]:
        """按绘制顺序把若干层叠成一张（None 项跳过）。"""
        arrays = [a for a in stack if a is not None]
        if not arrays:
            return None
        canvas = np.zeros((h, w, 4), dtype=np.float64)
        for src in arrays:
            alpha = src[..., 3:4].astype(np.float64) / 255.0
            canvas[..., :3] = src[..., :3] * alpha + canvas[..., :3] * (1.0 - alpha)
            canvas[..., 3:4] = alpha * 255.0 + canvas[..., 3:4] * (1.0 - alpha)
        return np.clip(canvas, 0, 255).astype(np.uint8)

    def _layer_array(self, layers: Dict[str, Dict], key: str) -> Optional[np.ndarray]:
        info = layers.get(key)
        if not isinstance(info, dict):
            return None
        path = info.get("path")
        if not path:
            return None
        cache = getattr(self, "_array_cache", None)
        if cache is not None and path in cache:
            return cache[path]
        arr = self._load_png(path)
        if cache is not None:
            cache[path] = arr
        return arr

    @staticmethod
    def _load_png(path) -> Optional[np.ndarray]:
        if not path or not Path(path).is_file():
            return None
        return np.array(Image.open(path).convert("RGBA"))

    def _store_layer(self, layers: Dict[str, Dict], dest: Path, name: str,
                     layer_arr: np.ndarray, rest_opacity: float = 1.0) -> Dict:
        """落盘一张合成层并登记与普通层同构的元数据。

        ``rest_opacity`` 是**静止姿态下的不透明度**：腮红/眼泪这类"淡入"表情层
        静止时为 0（不可见），由参数键形在运行时抬到 1。
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{name}.png"
        Image.fromarray(layer_arr, "RGBA").save(path)
        pixels = layer_arr[..., 3] > 0
        if pixels.any():
            ys, xs = np.nonzero(pixels)
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
            mean_color = tuple(int(c) for c in layer_arr[pixels][:, :3].mean(axis=0))
        else:
            bbox = (0, 0, 0, 0)
            mean_color = (0, 0, 0)
        layers[name] = {
            "name": name,
            "path": str(path),
            "bbox": bbox,
            "size": (int(bbox[2] - bbox[0] + 1), int(bbox[3] - bbox[1] + 1)),
            "pixel_count": int(pixels.sum()),
            "mean_color": mean_color,
            "occluded_by": [],
            "completed": False,
            "synthesized": True,
            "rest_opacity": float(rest_opacity),
        }
        log.debug(f"  layer {name:14s} pixels={layers[name]['pixel_count']:>7d} "
                  f"bbox={bbox} synthesized=True rest_opacity={rest_opacity}")
        return layers[name]

    def reorder_layers(
        self,
        layers: Dict[str, Dict],
        target_order: Optional[List[str]] = None,
    ) -> Dict[str, Dict]:
        """Return an ordered dict of layers keyed by the target draw order.

        Layers whose names do not appear in ``target_order`` are appended at
        the end in alphabetical order so no layer is lost.

        Args:
            layers: Dict of part name -> layer metadata.
            target_order: Desired order. Defaults to ``STANDARD_LAYER_ORDER``.

        Returns:
            New dict with keys ordered back-to-front.
        """
        from collections import OrderedDict

        order = list(target_order) if target_order is not None else list(self.STANDARD_LAYER_ORDER)
        ordered: "OrderedDict[str, Dict]" = OrderedDict()
        seen = set()
        for name in order:
            if name in layers:
                ordered[name] = layers[name]
                seen.add(name)
        for name in sorted(k for k in layers if k not in seen):
            ordered[name] = layers[name]
        return ordered

    def generate_layer_json(
        self,
        layers: Dict[str, Dict],
        output_path: str,
    ) -> str:
        """Serialize layer metadata to JSON.

        Args:
            layers: Dict of part name -> metadata (as from :meth:`compose`).
            output_path: Destination JSON path.

        Returns:
            Absolute path of the written JSON file.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ordered = self.reorder_layers(layers)
        payload = {
            "standard_order": self.STANDARD_LAYER_ORDER,
            "layer_count": len(ordered),
            "layers": [
                {
                    "name": info["name"],
                    "draw_index": idx,
                    "path": info["path"],
                    "bbox": list(info["bbox"]),
                    "size": list(info["size"]),
                    "pixel_count": info["pixel_count"],
                    "mean_color": list(info["mean_color"]),
                    "occluded_by": info.get("occluded_by", []),
                    "amodal_completed": bool(info.get("completed", False)),
                }
                for idx, info in enumerate(ordered.values())
            ],
        }
        with out.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.info(f"Wrote layer JSON -> {out}")
        return str(out.resolve())

    def generate_occlusion_map(
        self,
        part_masks: Dict[str, np.ndarray],
    ) -> Dict[str, Dict[str, List[str]]]:
        """For each layer, list layers it occludes and that occlude it.

        A layer ``B`` is considered to occlude layer ``A`` when ``B`` is
        drawn after ``A`` (appears later in :attr:`STANDARD_LAYER_ORDER`) and
        their masks intersect.

        Args:
            part_masks: part name -> boolean mask.

        Returns:
            ``{part: {"occludes": [...], "occluded_by": [...]}}``
        """
        order = self.STANDARD_LAYER_ORDER
        # Assign draw indices. Unknown parts sort after known ones alphabetically.
        names = list(part_masks.keys())
        known = [n for n in order if n in part_masks]
        unknown = sorted(n for n in names if n not in order)
        draw_seq = known + unknown

        # Pre-bool all masks
        masks = {n: np.asarray(part_masks[n], dtype=bool) for n in names}

        result: Dict[str, Dict[str, List[str]]] = {}
        for name in names:
            result[name] = {"occludes": [], "occluded_by": []}

        for i, a in enumerate(draw_seq):
            ma = masks[a]
            if not ma.any():
                continue
            for b in draw_seq[i + 1:]:
                mb = masks[b]
                if not mb.any():
                    continue
                # Fast pre-check via bounding boxes
                if not self._bbox_overlap(ma, mb):
                    continue
                if np.any(ma & mb):
                    # b is drawn in front of a -> b occludes a
                    result[a]["occluded_by"].append(b)
                    result[b]["occludes"].append(a)
        return result

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _extract_layer(img_arr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Return an ``(H, W, 4)`` uint8 array with only ``mask`` pixels."""
        out = np.zeros(img_arr.shape, dtype=np.uint8)
        out[mask] = img_arr[mask]
        return out

    @staticmethod
    def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
        """布尔掩码膨胀 ``radius`` 像素（无 cv2 时退化为原样返回）。"""
        try:
            import cv2  # type: ignore
        except ImportError:  # pragma: no cover
            return mask
        size = max(1, int(radius) * 2 + 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)

    @staticmethod
    def _erase_features(layer_arr: np.ndarray, mask: np.ndarray,
                        feature_mask: np.ndarray) -> np.ndarray:
        """把脸层里被五官盖住的像素换成周围肤色（alpha 不变）。

        为什么不直接把五官区域挖成透明：眨眼/闭嘴时会把脸上的洞露出来，透出背景。
        这里用「脸层自身可见像素」作为唯一已知区域做 inpaint，填充色因此只会来自
        额头/脸颊的皮肤，不会把头发或背景的颜色渗进来。
        """
        holes = np.asarray(mask, dtype=bool) & np.asarray(feature_mask, dtype=bool)
        if not holes.any():
            return layer_arr
        known = np.asarray(mask, dtype=bool) & ~holes
        if not known.any():
            return layer_arr
        try:
            import cv2  # type: ignore
        except ImportError:  # pragma: no cover
            fill = np.median(layer_arr[known][:, :3], axis=0).astype(np.uint8)
            layer_arr[holes, :3] = fill
            return layer_arr

        ys, xs = np.nonzero(mask)
        pad = 8
        h, w = mask.shape
        y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad + 1)
        x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad + 1)
        sub_src = np.ascontiguousarray(layer_arr[y0:y1, x0:x1, :3])
        # 「脸层可见像素」之外的区域也标成待补：否则 inpaint 会把画布上的黑色当已知色
        unknown = (~known[y0:y1, x0:x1]).astype(np.uint8) * 255
        bgr = cv2.cvtColor(sub_src, cv2.COLOR_RGB2BGR)
        filled = cv2.cvtColor(
            cv2.inpaint(bgr, unknown, 5, cv2.INPAINT_TELEA), cv2.COLOR_BGR2RGB)
        sub_holes = holes[y0:y1, x0:x1]
        view = layer_arr[y0:y1, x0:x1]
        view[sub_holes, :3] = filled[sub_holes]
        return layer_arr

    @staticmethod
    def _bbox_overlap(a: np.ndarray, b: np.ndarray) -> bool:
        """Return True if boolean masks *might* overlap (cheap bbox test)."""
        def bbox(m: np.ndarray):
            ys, xs = np.where(m)
            if len(xs) == 0:
                return (0, 0, 0, 0)
            return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        ax0, ay0, ax1, ay1 = bbox(a)
        bx0, by0, bx1, by1 = bbox(b)
        if ax0 > bx1 or bx0 > ax1 or ay0 > by1 or by0 > ay1:
            return False
        return True
