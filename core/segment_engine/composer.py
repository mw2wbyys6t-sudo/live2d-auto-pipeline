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
from typing import Dict, List, Optional

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
        # 是靠脸的洞透出来的。眼皮带一旦给那些洞填色，刘海就被涂成皮肤（实测
        # 1206 px）。把刘海排到脸/眼皮之后，它直接盖在上面，与 `preview.png` 的
        # 逐像素误差反而更小（MAE 1.5767 → 1.5695）。
        # 脸底层必须在**五官之前**（列表越靠后 = 画得越靠前）。
        #
        # 历史顺序把 face_base 排在 eyebrows/eyes 之后：那时 face 层里复制了眼睛
        # 像素，谁在前都看不出来。脸层被清成纯皮肤后（见 FACE_PARTS），顺序立刻
        # 显形——实测眼睛被脸盖成几块碎片。
        "face_base",
        "face",
        # 五官：真实分层器产出的名字（eyewhite_* / irides_* / eyebrow_*）都要列上，
        # 否则它们会落到"未列出→按字母序追加到最后"，被画到头发和衣服之上。
        "eyes", "eyes_left", "eyes_right",
        "eyewhite_left", "eyewhite_right",
        "irides_left", "irides_right",
        "eyelash_left", "eyelash_right",
        # 上眼睑：合成层（见 synthesize_face_geometry）。必须排在眼球**之后**
        # （列表越靠后 = 画得越靠前），否则闭眼时眼皮盖不住瞳孔；同时必须排在
        # 眉毛**之前**，否则眼皮带（从眼睛上方取的脸层像素）会把眉毛涂掉。
        "eyelid_left", "eyelid_right",
        # 刘海：必须排在眼睑**之后**（画在眼皮前），否则眼皮带的补色会盖住刘海。
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
    #: 立绘里没有眼皮画稿，闭眼只能把眼球压扁——眼睛变成一坨被压扁的色块。这里从
    #: 眼睛层上方**取脸层自己的皮肤**做一条带，闭眼时整条带下移盖住眼球：皮肤是原画
    #: 的像素，盖上去等于"H 眼皮"，而不是凭空造一块颜色。
    EYELID_SOURCES = (
        ("eyelid_left",
         ("irides_left", "eyewhite_left", "eyes_left", "eye_left")),
        ("eyelid_right",
         ("irides_right", "eyewhite_right", "eyes_right", "eye_right")),
    )
    #: 从眼睛层最上面复制几行做"睫毛线"。静止时必须与眼睛层的像素**逐字节相同**，
    #: 否则闭眼参数一动就会看到一条副本痕迹（这也是静止姿态逐像素不变的来源）。
    EYELID_LASH_ROWS = 2
    #: 眼睑带左右各外扩的像素数：调大可以让闭眼时眼睛边缘不留缝。
    EYELID_PAD = 1

    #: 口内层：从嘴线层取形状与色调，合成"口腔内侧"。
    MOUTH_CAVITY_SOURCES = ("mouth", "mouth_outer", "mouth_line")
    MOUTH_CAVITY_NAME = "mouth_cavity"
    #: 口内色 = 嘴线色向暖红偏移的比例（嘴线通常是近黑的墨线）。
    MOUTH_CAVITY_WARMTH = 0.45

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
        """合成「上眼睑」与「口内」两层，返回新增的层名。

        立绘里没有这两种画稿：眼睛是睁开的、嘴是闭着的。缺了它们，眨眼只能把眼球
        压扁，张嘴只能把墨线撑粗——参数确实动了，但表现不像"闭眼/张嘴"。

        静止姿态必须**逐像素不变**，所以两层都不是凭空画形状：
        * 眼睑 = 眼睛上方「脸层自己的像素」+ 眼睛层最上面 ``EYELID_LASH_ROWS`` 行；
          原位置、原像素，静止时叠在脸上等于原地重画；闭眼时整条带下移盖住眼球。
        * 口内 = 嘴线层的形状，填成偏暖的口内色；静止时正好被嘴唇完全遮住。

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
        return made

    def _synthesize_eyelid(self, name: str, sources: List[str],
                           layers: Dict[str, Dict], h: int,
                           w: int) -> Optional[np.ndarray]:
        """生成一条「眼皮带」：上方是脸层皮肤，下沿是眼球层顶部 ``lash`` 行。

        只复制 **alpha 正好 255** 的像素。这是"静止逐像素不变"的关键：半透明像素
        再叠一次会得到 `src·a + base·(1-a)`，与只画一层不同（实测会差 2 像素）。
        脸层/眼球层的抗锯齿边因此不复制——它们本来就在原层上。
        """
        face = self._layer_array(layers, "face")
        if face is None:
            face = self._layer_array(layers, "face_base")
        if face is None:
            return None
        eye = self._alpha_over([self._layer_array(layers, n) for n in sources], h, w)
        if eye is None:
            return None
        mask = eye[..., 3] > 8
        if not mask.any():
            return None
        ys, xs = np.nonzero(mask)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        travel = y1 - y0 + 1                     # 下移距离 = 眼高，正好盖满
        pad = max(0, int(self.EYELID_PAD))
        lash = max(1, int(self.EYELID_LASH_ROWS))
        bx0, bx1 = max(0, x0 - pad), min(w - 1, x1 + pad)
        by0 = max(0, y0 - travel)
        if by0 >= y0:
            return None

        lid = np.zeros((h, w, 4), dtype=np.uint8)
        # 皮肤带：脸层原像素；**只**给"静止时会被更靠前的层盖住"的洞补皮肤色。
        #
        # 脸层在刘海位置是**镂空**的（分出来的脸只有可见皮肤），而眼皮带必须整块
        # 不透光，否则闭眼时瞳孔会从洞里透出来。补色取"同一列最近的皮肤"，补出来
        # 是一块连续皮肤而不是平色。但不加条件地补会把这些洞在静止时也点亮——
        # 实测 147 px 在刘海缝里冒出肤色。所以只在 `cover`（画在眼皮之后、
        # 且真的存在的层）下面补，静止时逐像素不变。
        band_src = face[by0:y0, bx0:bx1 + 1]
        solid = band_src[..., 3] == 255
        if not solid.any():
            return None
        filled = self._nearest_fill(band_src[..., :3].astype(np.int16), solid)
        covered = self._cover_mask(layers, name, h, w)[by0:y0, bx0:bx1 + 1]
        paint = solid | covered
        # 没有覆盖的 1px 发丝缝**不补**：补了会在静止画面上留下几十个皮肤色噪点
        # （实测 66 px），而闭眼时那几列只是细线，读起来像眼皮褶。静止逐像素不变
        # 是更硬的要求。
        band = lid[by0:y0, bx0:bx1 + 1]
        band[..., :3] = filled.astype(np.uint8)
        band[..., 3] = np.where(paint, 255, 0)

        # 睫毛线：眼球层顶部 lash 行（随眼皮一起下移）。半透明像素不复制，
        # 否则叠两次会得到与只画一层不同的颜色（实测差 2 像素）。
        eye_rows = min(h, y0 + lash)
        top_src = eye[y0:eye_rows]
        top_solid = top_src[..., 3] == 255
        if top_solid.any():
            row_view = lid[y0:eye_rows]
            row_view[top_solid] = top_src[top_solid]
        return lid

    @staticmethod
    def _nearest_fill(rgb: np.ndarray, solid: np.ndarray) -> np.ndarray:
        """把 ``solid`` 之外的像素按**同列最近**的实心像素颜色补齐。"""
        out = rgb.copy()
        known = rgb[solid].astype(np.float64)
        fallback = (np.median(known, axis=0) if len(known)
                    else np.array([233.0, 214.0, 198.0]))
        rows, cols = solid.shape
        for c in range(cols):
            col = solid[:, c]
            if not col.any():
                out[:, c] = fallback.astype(np.int16)
                continue
            idx = np.nonzero(col)[0]
            for r in range(rows):
                if col[r]:
                    continue
                nearest = idx[int(np.abs(idx - r).argmin())]
                out[r, c] = out[nearest, c]
        return out

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

    def _cover_mask(self, layers: Dict[str, Dict], name: str,
                    h: int, w: int) -> np.ndarray:
        """``name`` **之后**绘制、且真的存在的层所占的像素（静止时能盖住的东西）。"""
        rank = {n: i for i, n in enumerate(self.STANDARD_LAYER_ORDER)}
        mine = rank.get(name, len(rank))
        cover = np.zeros((h, w), dtype=bool)
        for other in layers:
            if other == name or rank.get(other, len(rank)) <= mine:
                continue
            arr = self._layer_array(layers, other)
            if arr is not None:
                cover |= arr[..., 3] > 0
        return cover

    @staticmethod
    def _load_png(path) -> Optional[np.ndarray]:
        if not path or not Path(path).is_file():
            return None
        return np.array(Image.open(path).convert("RGBA"))

    def _store_layer(self, layers: Dict[str, Dict], dest: Path, name: str,
                     layer_arr: np.ndarray) -> Dict:
        """落盘一张合成层并登记与普通层同构的元数据。"""
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
        }
        log.debug(f"  layer {name:14s} pixels={layers[name]['pixel_count']:>7d} "
                  f"bbox={bbox} synthesized=True")
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
