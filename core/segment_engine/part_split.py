#!/usr/bin/env python3
"""
Pygmalion Studio - Left/Right Part Splitting

Semantic segmenters detect limbs as one coarse mask (``arms``, ``hands``,
``legs``). Live2D rigging needs each side as an independent layer, because a
left arm and a right arm deform under different parameters.

Splitting by a text prompt such as "left arm" is unreliable: open-vocabulary
detectors confuse the two sides and the character's left is the viewer's right.
This module therefore splits an already-detected mask along the character's
body midline, which is estimated from the face/neck box.

Side convention: ``_L`` is the **character's** left, which appears on the
viewer's right for a front-facing character (larger x).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np


class SplitNote(str):
    """与 ``str`` 完全兼容的拆分备注，额外携带机器可读的 ``code`` / ``part``。

    为什么不是普通 dataclass：现有的拆分函数返回 ``List[str]``，调用方与测试既做
    ``"肘部" in message`` 子串判断，也做 ``not warnings`` 空表判断，还会直接
    ``log.warning(message)``。继承 ``str`` 可以零成本保留这些语义，同时让 manifest
    拿到结构化字段，把「降级原因」从日志提升为可审计证据。
    """

    code: str
    part: Optional[str]

    def __new__(cls, message: str, code: str, part: Optional[str] = None) -> "SplitNote":
        obj = super().__new__(cls, message)
        obj.code = code
        obj.part = part
        return obj


#: 会让被拆分部件「不能按语义安全拆分」的机器码。命中即标 ``split_limited``，
#: 该层可继续作为导出候选，但不得宣称高质量（见 staging spec 2026-11-19 INV-A2）。
DEGRADATION_CODES = frozenset({
    "limb_no_midline",
    "limb_single_side",
    "eye_low_contrast",
    "eye_ratio_abnormal",
    "hair_front_back_failed",
    "depth_insufficient",
    "depth_ratio_unbalanced",
    "eye_detail_low_evidence",
    "lash_not_separable",
    "mouth_detail_low_evidence",
    "mouth_closed_line",
})

#: Coarse mask name -> canonical base name used for the split layers.
SIDED_SOURCE_PARTS: Dict[str, str] = {
    "arms": "arm",
    "arm": "arm",
    "hands": "hand",
    "hand": "hand",
    "legs": "leg",
    "leg": "leg",
    "sleeves": "sleeve",
    "sleeve": "sleeve",
    "socks": "sock",
    "sock": "sock",
    "boots": "boot",
    "boot": "boot",
    "eyebrows": "eyebrow",
    "eyes": "eye_white",
    "eyewhite": "eye_white",
    "irides": "eye_iris",
    "eyelash": "eyelash",
    "ears": "ear",
    "earwear": "earwear",
    "handwear": "handwear",
    "legwear": "sock",
    "footwear": "boot",
}

#: Mask names whose bounding box is used to estimate the body midline, in
#: order of preference. The face is the most reliable anchor.
_MIDLINE_ANCHORS: Tuple[str, ...] = ("face", "head", "neck", "clothes_top")

#: A side smaller than this fraction of the parent mask is treated as noise.
_MIN_SIDE_RATIO = 0.02

#: Edge-band ratios, as a fraction of the parent part's height. These are the
#: tuning knobs for derived garment pieces: smaller values give thinner bands.
CUFF_BAND_RATIO = 0.10
SKIRT_HEM_BAND_RATIO = 0.08

#: Minimum band height in pixels so small parts still produce a usable strip.
MIN_BAND_PX = 4

#: 一行至少要占该部件最宽行的这个比例，才算"实体行"。
#: 立绘里常有几根飘带／缎带尾巴伸到裙子下方很远；若按 ``rows.max()`` 取底边，
#: 8% 的裙摆带会整条落在飘带尖上（实测只剩 74px，随后被空层剪枝丢掉，
#: SkirtHem 物理链也跟着消失）。改用"实体行"范围后，带子切在裙体上。
DENSE_ROW_RATIO = 0.15

#: Garment pieces whose edge bands are promoted to their own layers.
#: ``name -> ((derived base, edge, band ratio of the parent height), ...)``
#: Multiple bands may be cut from one parent; they are taken in order from the
#: remaining mask, so later cuts never overlap earlier ones.
_BAND_DERIVATIONS: Dict[str, Tuple[Tuple[str, str, float], ...]] = {
    "arm_L": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "arm_R": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "sleeve_L": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "sleeve_R": (("cuff", "bottom", CUFF_BAND_RATIO),),
    "skirt": (("skirt_hem", "bottom", SKIRT_HEM_BAND_RATIO), ("waistband", "top", 0.08)),
    "skirt_body": (("skirt_hem", "bottom", SKIRT_HEM_BAND_RATIO), ("waistband", "top", 0.08)),
    "clothes_bottom": (("skirt_hem", "bottom", SKIRT_HEM_BAND_RATIO), ("waistband", "top", 0.08)),
    "clothes_top": (("collar", "top", 0.10),),
    "outer_body": (("collar", "top", 0.10),),
    "shirt": (("collar", "top", 0.10),),
    "boot_L": (("boot_cuff", "top", 0.15),),
    "boot_R": (("boot_cuff", "top", 0.15),),
}

#: Parts produced by geometric edge bands rather than by detection.
DERIVED_PARTS = frozenset({"cuff", "skirt_hem", "collar", "waistband", "boot_cuff",
                           "skirt_inner",
                           # P1 眼/口细节：全部由母掩码按像素证据再分，属派生层。
                           "eye_pupil", "eye_highlight",
                           "eyelash_upper", "eyelash_lower",
                           "teeth", "tongue", "lip_upper", "lip_lower"})

#: Eye masks that can be split into sclera (white) and iris by luminance.
EYE_SOURCES: Dict[str, str] = {
    "eyes_left": "left",
    "eyes_right": "right",
    "eye_left": "left",
    "eye_right": "right",
    "eye_white_L": "left",
    "eye_white_R": "right",
    "eye_white_left": "left",
    "eye_white_right": "right",
}

#: Hair masks that must end up split into front/back.
HAIR_FRONT = "hair_front"
HAIR_BACK = "hair_back"
#: 后发两侧的独立束（角色左 = 观察者右）。四条摆链靠这三个名字分工：
#: ``hair_back`` 走 ParamHairSwingBack，左右两束各自一条侧摆链。
HAIR_BACK_LEFT = "hair_back_left"
HAIR_BACK_RIGHT = "hair_back_right"
#: 前发左右两束（角色左 = 观察者右）。它们各走一条前发侧摆链。
HAIR_FRONT_LEFT = "hair_front_left"
HAIR_FRONT_RIGHT = "hair_front_right"

#: 前发分左右的门槛：某一侧像素 ≥ 前发总量的 8% 且 ≥ 300px。
#: 只按**连通块**归属分束，不做列切：前发本来就是几缕分开画的，按连通块归类
#: 不会产生新缝隙，也就不存在"摆动时从切缝裂开"的风险 —— 后发拒绝硬切正是
#: 因为这个（见 ``split_hair_strands`` 的说明）。
HAIR_FRONT_SIDE_MIN_RATIO = 0.08
HAIR_FRONT_SIDE_MIN_PX = 300

#: 后发分束的**自适应门槛**：每一束（含中间那束）至少要占后发总像素的这个比例，
#: 否则不切 —— 窄发/整块发被硬切成三份只会多出两条假缝。
HAIR_STRAND_MIN_RATIO = 0.12
#: 分割谷的判定：候选列的占用像素数 ≤ 该侧平均占用的这个比例才算「能分开」。
#: 1.0 表示不要求谷深（等于硬切），太松会把一整块平发切成三份。
HAIR_VALLEY_RATIO = 0.75


def _bbox_center_x(mask: np.ndarray) -> Optional[float]:
    cols = np.where(mask.any(axis=0))[0]
    if cols.size == 0:
        return None
    return float(cols.min() + cols.max()) / 2.0


def estimate_midline(part_masks: Dict[str, np.ndarray], width: int) -> float:
    """Estimate the vertical body midline in pixel coordinates."""
    for anchor in _MIDLINE_ANCHORS:
        mask = part_masks.get(anchor)
        if mask is None:
            continue
        mask = np.asarray(mask, dtype=bool)
        center = _bbox_center_x(mask)
        if center is not None and 0 <= center <= width:
            return center

    union = np.zeros((1, width), dtype=bool)
    for mask in part_masks.values():
        mask = np.asarray(mask, dtype=bool)
        if mask.ndim == 2:
            union = np.zeros(mask.shape, dtype=bool)
            for other in part_masks.values():
                other = np.asarray(other, dtype=bool)
                if other.shape == mask.shape:
                    union |= other
            center = _bbox_center_x(union)
            if center is not None:
                return center
            break

    return width / 2.0


def _component_labels(mask: np.ndarray):
    """Return a label image, preferring cv2 then scipy; None when unavailable."""
    try:
        import cv2  # type: ignore

        count, labels = cv2.connectedComponents(mask.astype(np.uint8))
        return labels if count > 1 else None
    except Exception:
        pass
    try:
        from scipy import ndimage  # type: ignore

        labels, count = ndimage.label(mask)
        return labels if count > 1 else None
    except Exception:
        return None


def _split_by_components(
    mask: np.ndarray,
    midline: float,
    minimum: int,
) -> Dict[str, np.ndarray]:
    """Assign each connected component to a side by its bbox centre.

    Components are never cut, so a limb drawn across the midline stays whole.
    """
    labels = _component_labels(mask)
    if labels is None:
        return {}

    left = np.zeros_like(mask, dtype=bool)
    right = np.zeros_like(mask, dtype=bool)
    found_left = found_right = False

    for label in range(1, int(labels.max()) + 1):
        component = labels == label
        if int(component.sum()) < minimum:
            continue
        cols = np.where(component.any(axis=0))[0]
        centre = float(cols.min() + cols.max()) / 2.0
        if centre >= midline:
            left |= component
            found_left = True
        else:
            right |= component
            found_right = True

    if not (found_left and found_right):
        return {}
    return {"L": left, "R": right}


def _split_by_column_gap(
    mask: np.ndarray,
    midline: float,
    minimum: int,
) -> Dict[str, np.ndarray]:
    """Split at the empty column closest to the body midline.

    Used when no labeling library is available. Only splits when a genuinely
    empty (or near-empty) column exists, so it cannot cut through a limb.
    """
    height, width = mask.shape
    columns = mask.sum(axis=0).astype(np.int64)
    occupied = np.where(columns > 0)[0]
    if occupied.size < 2:
        return {}

    left_edge, right_edge = int(occupied.min()), int(occupied.max())
    span = right_edge - left_edge
    if span < 8:
        return {}

    low = left_edge + max(1, int(span * 0.15))
    high = right_edge - max(1, int(span * 0.15))
    if high <= low:
        return {}

    best = low + int(np.argmin(columns[low : high + 1]))
    if int(columns[best]) > max(1, int(columns.max() * 0.05)):
        return {}

    xs = np.arange(width)[None, :]
    left = mask & (xs >= best)
    right = mask & (xs < best)
    if int(left.sum()) < minimum or int(right.sum()) < minimum:
        return {}
    return {"L": left, "R": right}


def split_mask_sides(
    mask: np.ndarray,
    midline: float,
    min_side_ratio: float = _MIN_SIDE_RATIO,
    use_components: bool = True,
) -> Dict[str, np.ndarray]:
    """Split one mask into character-left / character-right parts.

    Order of preference: connected components (keeps each limb whole), then an
    empty column gap, then a hard cut at the midline.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return {}

    total = int(mask.sum())
    minimum = max(1, int(total * min_side_ratio))

    if use_components:
        sides = _split_by_components(mask, midline, minimum)
        if sides:
            return sides

    sides = _split_by_column_gap(mask, midline, minimum)
    if sides:
        return sides

    width = mask.shape[1]
    xs = np.arange(width)[None, :]
    left = mask & (xs >= midline)
    right = mask & (xs < midline)

    result: Dict[str, np.ndarray] = {}
    if int(left.sum()) >= minimum:
        result["L"] = left
    if int(right.sum()) >= minimum:
        result["R"] = right
    return result


def split_limb_parts(
    part_masks: Dict[str, np.ndarray],
    size: Tuple[int, int],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Replace coarse limb masks with per-side masks.

    Args:
        part_masks: mapping of part name -> boolean mask.
        size: ``(width, height)`` of the canvas.

    Returns:
        ``(new_masks, warnings)``. Coarse keys that were split are removed and
        replaced by ``<base>_L`` / ``<base>_R`` entries.
    """
    width = int(size[0])
    midline = estimate_midline(part_masks, width)

    result: Dict[str, np.ndarray] = {}
    warnings: List[str] = []

    for name, mask in part_masks.items():
        base = SIDED_SOURCE_PARTS.get(str(name))
        if base is None:
            result[name] = mask
            continue

        sides = split_mask_sides(mask, midline)
        if not sides:
            result[name] = mask
            warnings.append(SplitNote(
                f"{name} 未能按中线拆分，保留整块", "limb_no_midline", name))
            continue

        for side, side_mask in sides.items():
            result[f"{base}_{side}"] = side_mask

        if len(sides) < 2:
            warnings.append(SplitNote(
                f"{name} 只检测到单侧（{next(iter(sides))}），另一侧缺失",
                "limb_single_side", name))

    return result, warnings


def split_eye_parts(
    part_masks: Dict[str, np.ndarray],
    image_rgb: np.ndarray,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Split each eye mask into sclera (``eyewhite_*``) and iris (``irides_*``).

    The split is luminance-based inside an already-detected eye mask, so no
    extra detector pass is needed. If the two clusters are not separable, the
    original eye mask is kept rather than inventing parts.
    """
    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []

    rgb = np.asarray(image_rgb)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return result, warnings

    luminance = (
        0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    ).astype(np.float32)

    for name, side in EYE_SOURCES.items():
        mask = result.pop(name, None)
        if mask is None:
            continue

        mask = np.asarray(mask, dtype=bool)
        if int(mask.sum()) < 30:
            result[name] = mask
            continue

        values = luminance[mask]
        low, high = np.percentile(values, 20), np.percentile(values, 80)
        if high - low < 10:
            result[name] = mask
            warnings.append(SplitNote(
                f"{name} 明暗差不足，未拆分眼白/瞳孔", "eye_low_contrast", name))
            continue

        threshold = (low + high) / 2.0
        iris = mask & (luminance <= threshold)
        sclera = mask & (luminance > threshold)
        iris_ratio = float(iris.sum()) / max(1, int(mask.sum()))
        if not iris.any() or not sclera.any() or not 0.08 <= iris_ratio <= 0.8:
            result[name] = mask
            warnings.append(SplitNote(
                f"{name} 眼白/瞳孔比例异常（{iris_ratio:.0%}），保留整块",
                "eye_ratio_abnormal", name))
            continue

        result[f"irides_{side}"] = iris
        result[f"eyewhite_{side}"] = sclera

    return result, warnings


def _valley_candidates(columns: np.ndarray, lo: int, hi: int) -> List[int]:
    """``[lo, hi)`` 内按占用升序给出候选切列（最多 3 个，且必须真的形成谷）。

    「谷」的判据是占用 ≤ 该侧平均占用的 ``HAIR_VALLEY_RATIO``。没有这个判据时
    一整块平直的后发也会被切成三份，凭空多出两条会在摆动时张开的假缝。
    """
    if hi <= lo:
        return []
    segment = columns[lo:hi]
    mean = float(segment.mean()) or 1.0
    picked: List[int] = []
    for offset in np.argsort(segment)[:3]:
        if int(segment[offset]) > HAIR_VALLEY_RATIO * mean:
            continue
        picked.append(lo + int(offset))
    return picked


def split_hair_strands(
    mask: np.ndarray,
    midline: float,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """把后发按「能分开的束」切成 中/左/右，束数自适应（1~3）。

    四条头发摆链的名字分工是固定的：``hair_back``（中）走慢摆，
    ``hair_back_left`` / ``hair_back_right`` 各走一条侧摆。但**不能无条件**切成
    三束 —— 窄发或整块平发被硬切，静止时看不出来，摆起来就会从切缝裂开。
    所以这里的束数由几何决定：

    * 两侧各找到一个「谷」且三束都够大 → 3 束（四束头发的完整形态）；
    * 只有一侧分得开 → 2 束（该侧独立，其余留在 ``hair_back``）；
    * 都不成立 → 原样返回，交给 ``split_hair_parts`` 保留单束 ``hair_back``。

    返回的掩码**互不重叠且并集等于输入**，所以切分本身不丢像素。
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return {}, []

    total = int(mask.sum())
    minimum = max(1, int(total * HAIR_STRAND_MIN_RATIO))
    columns = mask.sum(axis=0).astype(np.int64)
    occupied = np.where(columns > 0)[0]
    if occupied.size < 3:
        return {}, []
    x0, x1 = int(occupied.min()), int(occupied.max())
    if x1 - x0 < 4:
        return {}, []

    def part_px(lo: int, hi: int) -> int:
        return int(columns[lo:hi].sum())

    def band(lo: int, hi: int) -> np.ndarray:
        """``[lo, hi)`` 列范围的原尺寸掩码 —— 切列不能把数组裁窄，
        否则和其它层做按位与/或时形状对不上。"""
        out = np.zeros_like(mask)
        out[:, max(0, lo):max(0, hi)] = mask[:, max(0, lo):max(0, hi)]
        return out

    mid = int(round(float(midline)))
    mid = min(max(mid, x0 + 1), x1 - 1) if x1 - x0 >= 2 else x0

    # 三束：左切点在中线左边、右切点在中线右边；三束都必须 ≥ minimum。
    best3 = None
    for left_cut in _valley_candidates(columns, x0 + 1, mid):
        for right_cut in _valley_candidates(columns, mid + 1, x1):
            parts = (part_px(x0, left_cut), part_px(left_cut, right_cut),
                     part_px(right_cut, x1 + 1))
            if min(parts) < minimum:
                continue
            score = int(columns[left_cut]) + int(columns[right_cut])
            if best3 is None or score < best3[0]:
                best3 = (score, left_cut, right_cut)
    if best3 is not None:
        _, left_cut, right_cut = best3
        return {
            HAIR_BACK_RIGHT: band(0, left_cut),
            HAIR_BACK: band(left_cut, right_cut),
            HAIR_BACK_LEFT: band(right_cut, mask.shape[1]),
        }, ["后发按谷切成中/左/右三束（自适应：三束都够大）"]

    # 两束：只在分得开的那一侧切一刀，其余仍归 hair_back。
    best2 = None
    for cut in _valley_candidates(columns, x0 + 1, mid):
        if part_px(x0, cut) >= minimum and part_px(cut, x1 + 1) >= minimum:
            if best2 is None or int(columns[cut]) < best2[0]:
                best2 = (int(columns[cut]), cut, HAIR_BACK_RIGHT)
    for cut in _valley_candidates(columns, mid + 1, x1):
        if part_px(cut, x1 + 1) >= minimum and part_px(x0, cut) >= minimum:
            if best2 is None or int(columns[cut]) < best2[0]:
                best2 = (int(columns[cut]), cut, HAIR_BACK_LEFT)
    if best2 is not None:
        _, cut, side_name = best2
        if side_name == HAIR_BACK_RIGHT:
            outside, rest = band(0, cut), band(cut, mask.shape[1])
        else:
            outside, rest = band(cut, mask.shape[1]), band(0, cut)
        return {side_name: outside, HAIR_BACK: rest}, [
            f"后发只有一侧能分开，切成两束（{side_name} + {HAIR_BACK}）"]

    return {}, []


def split_hair_front_sides(
    mask: np.ndarray,
    midline: float,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """把前发按**连通块**归到左右两束，分不开就原样保留单束。

    为什么不像后发那样列切：后发是一整块连续的发片，列切会在摆动时从切缝
    裂开，所以那边用"谷 + 三束都够大"的严格判据；前发则本来就是几缕分开
    画出来的（实测这块前发有 5 个 ≥150px 的连通块），按连通块归类天然没有
    接缝，不需要额外重叠。

    判据：左右各 ≥ ``HAIR_FRONT_SIDE_MIN_PX`` 且 ≥ 前发总量的
    ``HAIR_FRONT_SIDE_MIN_RATIO``。任一侧不够就返回空字典。
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return {}, []
    total = int(mask.sum())
    labels = _component_labels(mask)
    if labels is None:
        return {}, []

    minimum = max(HAIR_FRONT_SIDE_MIN_PX, int(total * HAIR_FRONT_SIDE_MIN_RATIO))
    left = np.zeros_like(mask)
    right = np.zeros_like(mask)
    for index in range(1, int(labels.max()) + 1):
        component = labels == index
        if not component.any():
            continue
        xs = np.nonzero(component)[1]
        if float(xs.mean()) < float(midline):
            left |= component
        else:
            right |= component

    left_px, right_px = int(left.sum()), int(right.sum())
    if left_px < minimum or right_px < minimum:
        return {}, []
    return {HAIR_FRONT_LEFT: left, HAIR_FRONT_RIGHT: right}, [
        f"前发按连通块分成左右两束（{left_px} / {right_px} px）"]


def split_hair_parts(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Ensure hair is split into distinct front/back masks（并尽量拆出左右两束）。

    Some backends emit one hair mask and copy it into both slots, which draws
    the same pixels twice. When the two masks are identical (or one is
    missing), the hair mass is split against the face box: hair covering the
    face is front hair, the rest is back hair.

    前后确定以后，后发交给 :func:`split_hair_strands` 按几何拆成中/左/右，
    前发交给 :func:`split_hair_front_sides` 按连通块归到左右两束 —— 两者都是
    自适应：拆不开就保持单束，绝不硬切出假缝。
    """
    result, warnings = _ensure_hair_front_back(part_masks)

    front = result.get(HAIR_FRONT)
    if front is not None:
        front_mask = np.asarray(front, dtype=bool)
        if front_mask.any():
            midline = estimate_midline(result, front_mask.shape[1])
            sides, side_warnings = split_hair_front_sides(front_mask, midline)
            if sides:
                result.pop(HAIR_FRONT, None)
                result.update(sides)
                warnings.extend(side_warnings)

    back = result.get(HAIR_BACK)
    if back is None:
        return result, warnings
    back_mask = np.asarray(back, dtype=bool)
    if not back_mask.any():
        return result, warnings
    midline = estimate_midline(result, back_mask.shape[1])
    strands, strand_warnings = split_hair_strands(back_mask, midline)
    if strands:
        result.pop(HAIR_BACK, None)
        result.update(strands)
        warnings.extend(strand_warnings)
    return result, warnings


def _ensure_hair_front_back(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """把「同一块掩码同时当刘海和后发」的情况拆成前后两份。"""
    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []

    front = result.get(HAIR_FRONT)
    back = result.get(HAIR_BACK)
    if front is None and back is None:
        return result, warnings

    front_mask = np.asarray(front, dtype=bool) if front is not None else None
    back_mask = np.asarray(back, dtype=bool) if back is not None else None
    if front_mask is not None and back_mask is not None and not np.array_equal(front_mask, back_mask):
        return result, warnings

    shape = front_mask.shape if front_mask is not None else back_mask.shape
    hair = np.zeros(shape, dtype=bool)
    if front_mask is not None:
        hair |= front_mask
    if back_mask is not None:
        hair |= back_mask
    if not hair.any():
        return result, warnings

    face = result.get("face")
    face_mask = np.asarray(face, dtype=bool) if face is not None else None

    if face_mask is not None and face_mask.shape == shape and face_mask.any():
        new_front = hair & face_mask
        new_back = hair & ~face_mask
        source = "面部包围盒"
    else:
        rows = np.where(hair.any(axis=1))[0]
        top, bottom = int(rows.min()), int(rows.max())
        cut = top + max(1, int((bottom - top + 1) * 0.4))
        new_front = hair.copy()
        new_front[cut:, :] = False
        new_back = hair.copy()
        new_back[:cut, :] = False
        source = "上部 40%"

    if not new_front.any() or not new_back.any():
        warnings.append(SplitNote(
            "头发前后拆分失败，保留原掩码", "hair_front_back_failed", "hair"))
        return result, warnings

    result[HAIR_FRONT] = new_front
    result[HAIR_BACK] = new_back
    warnings.append(SplitNote(
        f"头发前后由同一掩码按{source}拆分", "hair_front_back_inferred", "hair"))
    return result, warnings


#: Parent masks that can be split into front/back by a depth map.
#: Larger depth = nearer to the viewer = front layer.
DEPTH_SPLIT_PARENTS: Dict[str, Tuple[str, str]] = {
    "hair": ("hair_front", "hair_back"),
    "hair_mid": ("hair_front", "hair_back"),
    "skirt": ("skirt_front", "skirt_back"),
    "skirt_body": ("skirt_front", "skirt_back"),
    "clothes_bottom": ("bottomwear_front", "bottomwear_back"),
}


def _normalize_depth(depth_map) -> Optional[np.ndarray]:
    depth = np.asarray(depth_map, dtype=np.float32)
    if depth.ndim != 2 or depth.size == 0:
        return None
    finite = depth[np.isfinite(depth)]
    if finite.size == 0:
        return None
    peak = float(np.nanmax(finite))
    if peak > 1.0:
        depth = depth / peak
    return depth


def split_depth_parts(
    part_masks: Dict[str, np.ndarray],
    depth_map,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Split selected parts into front/back using a per-pixel depth map.

    No depth map means no split: this function never invents depth values.
    """
    depth = _normalize_depth(depth_map)
    if depth is None:
        return part_masks, ["深度图不可用，跳过前后分层"]

    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []

    for parent, (front_name, back_name) in DEPTH_SPLIT_PARENTS.items():
        mask = result.get(parent)
        if mask is None:
            continue

        mask = np.asarray(mask, dtype=bool)
        if mask.shape != depth.shape:
            warnings.append(SplitNote(
                f"{parent} 与深度图尺寸不一致，跳过", "depth_size_mismatch", parent))
            continue
        total = int(mask.sum())
        if total < 100:
            continue

        values = depth[mask]
        low, high = np.percentile(values, 10), np.percentile(values, 90)
        if high - low < 0.02:
            warnings.append(SplitNote(
                f"{parent} 深度差不足，未拆前后", "depth_insufficient", parent))
            continue

        threshold = float(np.median(values))
        front = mask & (depth >= threshold)
        back = mask & (depth < threshold)
        if int(front.sum()) < total * 0.1 or int(back.sum()) < total * 0.1:
            warnings.append(SplitNote(
                f"{parent} 前后比例失衡，保留整块", "depth_ratio_unbalanced", parent))
            continue

        result.pop(parent, None)
        result[front_name] = result.get(front_name, np.zeros_like(mask)) | front
        result[back_name] = result.get(back_name, np.zeros_like(mask)) | back
        warnings.append(SplitNote(
            f"{parent} 按深度拆为 {front_name}/{back_name}", "depth_split_done", parent))

    return result, warnings


def attach_depths(
    layers: Dict[str, Dict],
    part_masks: Dict[str, np.ndarray],
    depth_map,
) -> Dict[str, Dict]:
    """Attach each layer's mean depth so ordering can use depth instead of z."""
    depth = _normalize_depth(depth_map)
    if depth is None:
        return layers

    for name, info in layers.items():
        mask = part_masks.get(name)
        if mask is None:
            continue
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != depth.shape or not mask.any():
            continue
        info["depth"] = round(float(depth[mask].mean()), 6)
    return layers


def _side_suffix(name: str) -> Optional[str]:
    lowered = str(name).lower()
    for suffix, side in (("_left", "L"), ("_right", "R"), ("_l", "L"), ("_r", "R")):
        if lowered.endswith(suffix):
            return side
    return None


def _edge_band(mask: np.ndarray, edge: str, ratio: float) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Return ``(band, remainder)`` for the top or bottom band of a mask."""
    mask = np.asarray(mask, dtype=bool)
    rows = np.where(mask.any(axis=1))[0]
    if rows.size == 0:
        return None, None

    # 边缘按"实体行"算，而不是最上/最下任意一行：细长的飘带尾巴会把范围
    # 拉长一百多行，导致按比例切出来的带子落在尾巴上（实测 74px）。
    widths = mask.sum(axis=1)
    widest = int(widths.max())
    threshold = max(1, int(round(widest * DENSE_ROW_RATIO)))
    dense = np.where(widths >= threshold)[0]
    solid = dense if dense.size >= 2 else rows

    top, bottom = int(solid.min()), int(solid.max())
    height = bottom - top + 1
    band_height = max(MIN_BAND_PX, int(round(height * ratio)))
    if band_height >= height:
        return None, None

    band = np.zeros_like(mask, dtype=bool)
    if edge == "bottom":
        band[bottom - band_height + 1 : bottom + 1, :] = mask[bottom - band_height + 1 : bottom + 1, :]
    else:
        band[top : top + band_height, :] = mask[top : top + band_height, :]

    return band, mask & ~band


def split_garment_details(
    part_masks: Dict[str, np.ndarray],
    size: Tuple[int, int],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Promote cuffs, skirt hem and collar into independent layers.

    These are geometric bands cut from an already-detected garment mask. They
    are honest *derived* pieces, not learned detections, and are marked as such
    in the manifest.
    """
    width = int(size[0])
    midline = estimate_midline(part_masks, width)

    result: Dict[str, np.ndarray] = {}
    derived: List[str] = []

    def _merge(name: str, mask: np.ndarray) -> None:
        if name in result:
            result[name] = result[name] | mask
        else:
            result[name] = mask

    for name, mask in part_masks.items():
        specs = _BAND_DERIVATIONS.get(str(name))
        if specs is None:
            _merge(str(name), np.asarray(mask, dtype=bool))
            continue

        side = _side_suffix(str(name))
        remaining = np.asarray(mask, dtype=bool)

        for base, edge, ratio in specs:
            band, remainder = _edge_band(remaining, edge, ratio)
            if band is None or not band.any() or remainder is None or not remainder.any():
                continue

            if base == "skirt_hem":
                halves = split_mask_sides(band, midline, min_side_ratio=0.25)
                if len(halves) == 2:
                    for half_side, half_mask in halves.items():
                        _merge(f"{base}_{half_side}", half_mask)
                        derived.append(f"{base}_{half_side}")
                    remaining = remainder
                    continue

            derived_name = f"{base}_{side}" if side else base
            _merge(derived_name, band)
            derived.append(derived_name)
            remaining = remainder

        _merge(str(name), remaining)

    return result, derived


# ---------------------------------------------------------------------------
# 部件细化：刘海分束 / 后侧发再分束 / 手臂分段 / 裙内层
#
# 这一组函数只做**几何/规则层面能可靠实现**的细化——输入是已经分好的布尔掩码，
# 不引入任何新的学习模型。四条硬约束写在最前面：
#
# 1. 每处派生都有面积阈值（命名常量 + 注释里的依据）；
# 2. 拆不出来就原样保留原层名，绝不产出空层；
# 3. 切分出来的子层互不重叠、并集等于原层（不丢一个像素，也不会「吃掉」原层）；
# 4. 派生名必须登记进 ``composer.STANDARD_LAYER_ORDER``，否则会按字母序落到
#    最上层（画在脸和衣服之上）。
# ---------------------------------------------------------------------------

#: 刘海（``hair_front`` / ``hair_bangs``）可以被拆成左右两束的源层名。
HAIR_BANGS_SOURCES: Tuple[str, ...] = ("hair_front", "hair_bangs")

#: 刘海每侧至少占刘海总像素的这个比例。依据：本机实测一张 1024 宽立绘的刘海
#: 由 13 个连通域组成，右侧主体 80.3%、左侧独立发绺 19.7%，取 15% 刚好接住
#: 后者；再小的多是抗锯齿碎点，硬切出来只会得到 1~2px 宽的假发丝。
HAIR_BANGS_SIDE_MIN_RATIO = 0.15
#: 刘海每侧的绝对下限（小画布上比例值可能只剩几个像素）。
HAIR_BANGS_SIDE_MIN_PX = 120

#: 后发侧束（``hair_back_left`` / ``hair_back_right``）可以被再分束的源层名。
HAIR_SUBSTRAND_SOURCES: Tuple[str, ...] = ("hair_back_left", "hair_back_right")

#: 侧束再分出的每一束至少占该侧面积的比例。侧束本身是一整条发绺，硬切两半会
#: 让摆动时的接缝露出来，所以要求两束都占得住（各 30%）。
HAIR_SUBSTRAND_MIN_RATIO = 0.30
#: 侧束再分束的绝对下限。
HAIR_SUBSTRAND_MIN_PX = 150
#: 单块连通域内部找「谷」的深度下限：候选列的占用 ≤ 平均占用的这个比例才算
#: 分得开。1.0 等于硬切，会把一整条平发切成两条会张开的假缝。
HAIR_SUBSTRAND_VALLEY_RATIO = 0.70

#: 可以被肘部切成上臂/小臂的层名（角色左 = ``_L``）。
ARM_SEGMENT_SOURCES: Tuple[str, ...] = ("arm_L", "arm_R")

#: 手臂低于这个高度（像素）不拆。依据：肘部要求上下两段各留出足够的可动空间，
#: 40px 已是最小可用高度的一半；再矮的手臂分出来只是两块 5px 的碎条。
ARM_SEGMENT_MIN_HEIGHT_PX = 40
#: 每一段至少占原手臂面积的比例。
ARM_SEGMENT_MIN_RATIO = 0.20
#: 每一段的绝对下限。
ARM_SEGMENT_MIN_PX = 150

#: 可以被膝盖切成大腿/小腿的层名（角色左 = ``_L``）。
LEG_SEGMENT_SOURCES: Tuple[str, ...] = ("leg_L", "leg_R")

#: 与手臂同一组阈值：大腿/小腿的可动空间需求同量级，太矮的腿分出来只是碎条。
LEG_SEGMENT_MIN_HEIGHT_PX = 40
LEG_SEGMENT_MIN_RATIO = 0.20
LEG_SEGMENT_MIN_PX = 150

#: 会被拆出 ``skirt_inner`` 的下装层名：旧管线的 ``skirt`` / ``skirt_body`` 与
#: 实际在用的 ``clothes_bottom``（19 类后端没有更细的下装名）。
SKIRT_INNER_SOURCES: Tuple[str, ...] = ("skirt", "skirt_body", "clothes_bottom")

#: 裙内层的腐蚀半径 = 主体包围盒短边的这个比例。依据：本机立绘的下装掩码是
#: **带镂空的裙形**（bbox 填充率 51%），8% 腐蚀后只剩 4.4% 像素、无法成层；
#: 4% 能在这类镂空裙形上得到约 13% 的内层核心，在 1000px 级立绘上对应一道
#: 20~40px 的内衬宽度，同时保证外轮廓仍然占主体。
SKIRT_INNER_ERODE_RATIO = 0.04
#: 内层占主体的面积下限：低于它说明腐蚀把可用面积吃光（细裙/短裙），不拆。
SKIRT_INNER_MIN_RATIO = 0.12
#: 内层占主体的面积上限：高于它说明腐蚀几乎没生效（内层≈外层，等于复制一层）。
#: 取 90%：实心裙形在 4% 腐蚀下内层约 87%，那是正常的「薄壳 + 核心」，不是复制品。
SKIRT_INNER_MAX_RATIO = 0.90

#: 派生子层自身必须保持连贯：最大连通块至少占子层自身的这个比例。
#: 与质量门的 ``LARGEST_CC_MIN``（layer_quality.py，0.60）同源——低于它的子层
#: 会被直接报成「碎片化」缺陷，等于用一次细化换来一条质量告警。源掩码本来就
#: 碎（碎发、手套反光、抗锯齿点）时，保持原层比硬拆诚实。
DERIVED_CHILD_MIN_LARGEST_RATIO = 0.60


def _is_coherent(mask: np.ndarray) -> bool:
    """子层是否够连贯（最大连通块占比 ≥ ``DERIVED_CHILD_MIN_LARGEST_RATIO``）。

    没有可用的标签库时无法度量，按「不阻断」处理（与拆分本身同一条兜底策略）。
    """
    mask = np.asarray(mask, dtype=bool)
    total = int(mask.sum())
    if total == 0:
        return False
    parts = _component_parts(mask)
    if not parts:
        return True
    return max(part[1] for part in parts) >= total * DERIVED_CHILD_MIN_LARGEST_RATIO


def _component_parts(mask: np.ndarray) -> List[Tuple[np.ndarray, int, float, int, int]]:
    """把掩码拆成连通域清单：``(component, area, centre_x, x0, x1)``。

    与 :func:`_split_by_components` 不同，这里**不丢弃小连通域**：调用方负责把
    它们归到某一侧，切分后的并集因此仍等于输入（不丢像素是硬约束）。
    没有可用的标签库时返回空列表，调用方据此放弃拆分。
    """
    mask = np.asarray(mask, dtype=bool)
    labels = _component_labels(mask)
    if labels is None:
        return []

    parts: List[Tuple[np.ndarray, int, float, int, int]] = []
    for label in range(1, int(labels.max()) + 1):
        component = labels == label
        area = int(component.sum())
        if area == 0:
            continue
        cols = np.where(component.any(axis=0))[0]
        x0, x1 = int(cols.min()), int(cols.max())
        parts.append((component, area, (x0 + x1) / 2.0, x0, x1))
    return parts


def _partition_by_midline(
    mask: np.ndarray,
    midline: float,
) -> Tuple[Dict[str, np.ndarray], int]:
    """按连通域中心把掩码分到中线两侧，返回 ``({left,right}, 连通域数)``。

    ``left`` 是**角色左**（观察者右，列号更大），与本模块 ``_L`` 的约定一致。
    连接域按整块归属，不会被中线切断——硬切断会在一块造型上留下静止时就
    看得见的直缝。
    """
    parts = _component_parts(mask)
    if not parts:
        return {}, 0
    left = np.zeros(mask.shape, dtype=bool)
    right = np.zeros(mask.shape, dtype=bool)
    for component, _area, centre, _x0, _x1 in parts:
        if centre >= midline:
            left |= component
        else:
            right |= component
    return {"left": left, "right": right}, len(parts)


def _split_strand_in_two(
    mask: np.ndarray,
    minimum: int,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """把一条发束分成左右两段；分不开返回 ``None``。

    优先在连通域之间的**最大横向间隙**处切（不切断任何一块）；只有一整块连通
    域时才退回列投影找谷（见 ``HAIR_SUBSTRAND_VALLEY_RATIO``）。两条路都要求
    两段面积 ≥ ``minimum``，否则宁可整条保留。
    """
    mask = np.asarray(mask, dtype=bool)
    parts = _component_parts(mask)

    if len(parts) >= 2:
        ordered = sorted(parts, key=lambda item: item[2])
        best = None
        for index in range(len(ordered) - 1):
            gap = ordered[index + 1][3] - ordered[index][4]
            if best is None or gap > best[0]:
                best = (gap, index)
        if best is None:
            return None
        cut_index = best[1]
        lo = np.zeros(mask.shape, dtype=bool)
        hi = np.zeros(mask.shape, dtype=bool)
        for index, (component, *_rest) in enumerate(ordered):
            target = lo if index <= cut_index else hi
            target |= component
        if int(lo.sum()) < minimum or int(hi.sum()) < minimum:
            return None
        return lo, hi

    if len(parts) != 1:
        return None

    columns = mask.sum(axis=0).astype(np.int64)
    x0, x1 = parts[0][3], parts[0][4]
    span = x1 - x0
    if span < 8:
        return None
    low = x0 + max(1, int(span * 0.2))
    high = x1 - max(1, int(span * 0.2))
    if high <= low:
        return None

    mean = float(columns[x0:x1 + 1].mean()) or 1.0
    for offset in np.argsort(columns[low:high + 1])[:3]:
        cut = low + int(offset)
        if int(columns[cut]) > HAIR_SUBSTRAND_VALLEY_RATIO * mean:
            continue
        lo = np.zeros(mask.shape, dtype=bool)
        hi = np.zeros(mask.shape, dtype=bool)
        lo[:, :cut] = mask[:, :cut]
        hi[:, cut:] = mask[:, cut:]
        if int(lo.sum()) >= minimum and int(hi.sum()) >= minimum:
            return lo, hi
    return None


def split_hair_bangs_strands(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """把刘海按「连通域 + 中线列投影」拆成 ``*_left`` / ``*_right``。

    只有一块连通域、或某一侧面积低于阈值时**保留原名不拆**：刘海主体是一条
    平直发块时，硬切成左右两半只会在眨眼/摆发时张开一条没有画稿的缝。
    """
    result: Dict[str, np.ndarray] = {}
    warnings: List[str] = []
    midline: Optional[float] = None

    for name, mask in part_masks.items():
        key = str(name)
        if key not in HAIR_BANGS_SOURCES:
            result[key] = mask
            continue

        mask = np.asarray(mask, dtype=bool)
        total = int(mask.sum())
        if total == 0:
            result[key] = mask
            continue
        if midline is None:
            midline = estimate_midline(part_masks, mask.shape[1])

        sides, components = _partition_by_midline(mask, midline or 0.0)
        minimum = max(HAIR_BANGS_SIDE_MIN_PX, int(total * HAIR_BANGS_SIDE_MIN_RATIO))
        if components < 2 or len(sides) != 2:
            result[key] = mask
            continue
        if int(sides["left"].sum()) < minimum or int(sides["right"].sum()) < minimum:
            result[key] = mask
            continue
        if not (_is_coherent(sides["left"]) and _is_coherent(sides["right"])):
            # 某一侧自己就是一堆碎发：硬拆只会把「碎片化」缺陷从母层搬到子层。
            result[key] = mask
            continue

        result[f"{key}_left"] = sides["left"]
        result[f"{key}_right"] = sides["right"]
        warnings.append(
            f"{key} 拆成左右两束（左 {int(sides['left'].sum())}px / "
            f"右 {int(sides['right'].sum())}px）"
        )

    return result, warnings


def split_hair_side_strands(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """在 ``hair_back_left/right`` 内部再拆出一束（``*_inner`` / ``*_outer``）。

    命名策略：**较大的一束保留原名**。原因是绑定层的 ``HairSideSwing_L/R``
    只精确匹配 ``hair_back_left`` / ``hair_back_right``，两束都改名会让现有侧摆
    链失去全部目标；多出来的一束按位置叫 ``_inner`` / ``_outer``，等变形器支持
    多目标后再接进来。
    """
    result: Dict[str, np.ndarray] = {}
    warnings: List[str] = []
    midline: Optional[float] = None

    for name, mask in part_masks.items():
        key = str(name)
        if key not in HAIR_SUBSTRAND_SOURCES:
            result[key] = mask
            continue

        mask = np.asarray(mask, dtype=bool)
        total = int(mask.sum())
        if total == 0:
            result[key] = mask
            continue
        minimum = max(HAIR_SUBSTRAND_MIN_PX, int(total * HAIR_SUBSTRAND_MIN_RATIO))
        two = _split_strand_in_two(mask, minimum)
        if two is None:
            result[key] = mask
            continue

        first, second = two
        if int(first.sum()) >= int(second.sum()):
            main, extra = first, second
        else:
            main, extra = second, first
        if not (_is_coherent(main) and _is_coherent(extra)):
            result[key] = mask
            continue

        if midline is None:
            midline = estimate_midline(part_masks, mask.shape[1])
        centre_main = _bbox_center_x(main)
        centre_extra = _bbox_center_x(extra)
        if centre_main is None or centre_extra is None:
            result[key] = mask
            continue
        suffix = "inner" if abs(centre_extra - midline) < abs(centre_main - midline) else "outer"

        result[key] = main
        result[f"{key}_{suffix}"] = extra
        warnings.append(
            f"{key} 再分出一束 {key}_{suffix}（{int(extra.sum())}px）")

    return result, warnings


def _split_at_midheight(
    mask: np.ndarray,
    min_height_px: int,
    min_ratio: float,
    min_px: int,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """按高度中点把一条肢体切成上下两段；不满足可动条件时返回 ``None``。

    手臂与腿共用同一条判据：太矮、某段过小、或有碎块（不连贯）都不拆，
    原层原样保留 —— 硬切出来的两段会在变形时露缝。
    """
    mask = np.asarray(mask, dtype=bool)
    rows = np.where(mask.any(axis=1))[0]
    if rows.size == 0:
        return None
    top, bottom = int(rows.min()), int(rows.max())
    height = bottom - top + 1
    if height < min_height_px:
        return None

    total = int(mask.sum())
    minimum = max(min_px, int(total * min_ratio))
    cut = top + height // 2
    upper = np.zeros(mask.shape, dtype=bool)
    lower = np.zeros(mask.shape, dtype=bool)
    upper[top:cut, :] = mask[top:cut, :]
    lower[cut:bottom + 1, :] = mask[cut:bottom + 1, :]
    if int(upper.sum()) < minimum or int(lower.sum()) < minimum:
        return None
    if not (_is_coherent(upper) and _is_coherent(lower)):
        # 掩码本身碎（手套/袖口反光被算进来）时不拆：两段都会带上碎片。
        return None
    return upper, lower


def split_limb_segments(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """把 ``arm_L`` / ``arm_R`` 在肘部（高度中点）拆成上臂与小臂。

    摆臂时上臂跟着肩膀走、小臂再叠一层惯性，两段共用一块网格只能二选一。
    手臂太矮或两段面积过小时不拆，原层原样保留。
    """
    result: Dict[str, np.ndarray] = {}
    warnings: List[str] = []

    for name, mask in part_masks.items():
        key = str(name)
        if key not in ARM_SEGMENT_SOURCES:
            result[key] = mask
            continue

        split = _split_at_midheight(
            mask, ARM_SEGMENT_MIN_HEIGHT_PX, ARM_SEGMENT_MIN_RATIO,
            ARM_SEGMENT_MIN_PX)
        if split is None:
            result[key] = mask
            continue

        upper, lower = split
        side = key.rsplit("_", 1)[-1]
        result[f"arm_upper_{side}"] = upper
        result[f"arm_lower_{side}"] = lower
        warnings.append(
            f"{key} 按肘部拆成 arm_upper_{side} / arm_lower_{side}"
            f"（{int(upper.sum())}px / {int(lower.sum())}px）")

    return result, warnings


def split_leg_segments(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """把 ``leg_L`` / ``leg_R`` 在膝盖（高度中点）拆成大腿/小腿。

    与手臂同理：膝部是大腿/小腿两级动作的分界。腿太矮或两段过小时不拆，
    原层原样保留（缺段会在 manifest 里按 limb chain 报未齐全）。
    """
    result: Dict[str, np.ndarray] = {}
    warnings: List[str] = []

    for name, mask in part_masks.items():
        key = str(name)
        if key not in LEG_SEGMENT_SOURCES:
            result[key] = mask
            continue

        split = _split_at_midheight(
            mask, LEG_SEGMENT_MIN_HEIGHT_PX, LEG_SEGMENT_MIN_RATIO,
            LEG_SEGMENT_MIN_PX)
        if split is None:
            result[key] = mask
            continue

        upper, lower = split
        side = key.rsplit("_", 1)[-1]
        result[f"thigh_{side}"] = upper
        result[f"calf_{side}"] = lower
        warnings.append(
            f"{key} 按膝盖拆成 thigh_{side} / calf_{side}"
            f"（{int(upper.sum())}px / {int(lower.sum())}px）")

    return result, warnings


def _erode_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    """二值腐蚀 ``radius`` 像素（cv2 椭圆核；无 cv2 时按 4 邻域迭代）。"""
    mask = np.asarray(mask, dtype=bool)
    radius = max(1, int(radius))
    try:
        import cv2  # type: ignore

        size = radius * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        return cv2.erode(mask.astype(np.uint8), kernel).astype(bool)
    except Exception:
        out = mask.copy()
        for _ in range(radius):
            nxt = out.copy()
            nxt[1:, :] &= out[:-1, :]
            nxt[:-1, :] &= out[1:, :]
            nxt[:, 1:] &= out[:, :-1]
            nxt[:, :-1] &= out[:, 1:]
            out = nxt
        return out


def split_skirt_inner(
    part_masks: Dict[str, np.ndarray],
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """按内外轮廓把下装拆出 ``skirt_inner``（内层）。

    内层 = 主体腐蚀后的核心，外轮廓 = 主体减去内层。两者**不重叠、并集等于
    原层**，所以静止画面逐像素不变，只是多了一层可以在摆裙时单独驱动的内衬。
    腐蚀把整块吃光、或内层碎成多块时保留原层（宁可不拆，也不交半层）。
    """
    result: Dict[str, np.ndarray] = {}
    warnings: List[str] = []

    for name, mask in part_masks.items():
        key = str(name)
        if key not in SKIRT_INNER_SOURCES:
            result[key] = mask
            continue

        mask = np.asarray(mask, dtype=bool)
        total = int(mask.sum())
        if total == 0:
            result[key] = mask
            continue

        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        if rows.size == 0 or cols.size == 0:
            result[key] = mask
            continue
        short = min(int(rows.max() - rows.min() + 1), int(cols.max() - cols.min() + 1))
        radius = max(2, int(round(short * SKIRT_INNER_ERODE_RATIO)))

        eroded = _erode_mask(mask, radius)
        # 只把腐蚀后**最大的一块**当内层：剩下的零碎小岛留在外轮廓里，并集
        # 仍然等于原层；内层因此不会碎成多块，被质量门的「碎片化」判定报缺陷。
        parts = _component_parts(eroded)
        if not parts:
            result[key] = mask
            continue
        inner = max(parts, key=lambda part: part[1])[0]
        area = int(inner.sum())
        if area < max(1, int(total * SKIRT_INNER_MIN_RATIO)):
            result[key] = mask
            continue
        if area > int(total * SKIRT_INNER_MAX_RATIO):
            result[key] = mask
            continue

        outer = mask & ~inner
        if not outer.any():
            result[key] = mask
            continue

        result[key] = outer
        if "skirt_inner" in result:
            result["skirt_inner"] = result["skirt_inner"] | inner
        else:
            result["skirt_inner"] = inner
        warnings.append(
            f"{key} 按内外轮廓拆出 skirt_inner（内层 {area}px / 外轮廓 "
            f"{int(outer.sum())}px）")

    return result, warnings


def refine_part_masks(
    part_masks: Dict[str, np.ndarray],
    image_rgb: Optional[np.ndarray] = None,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """对已分好的部件做几何细化：刘海分束 / 后侧发再分束 / 手臂分段 / 裙内层。

    必须在 ``split_hair_parts`` 与 ``split_garment_details`` **之后**调用：
    前者的前后发与侧束、后者的裙摆与腰带都已经切好，这里的阈值才是在真实
    剩余面积上判断的（否则会被即将切走的裙摆/袖口撑大）。

    传入 ``image_rgb`` 时还会做 P1 眼/口细节拆分（瞳孔/高光/睫毛、牙齿/舌头/
    上下唇）；不传时行为与原来完全一致，便于旧调用方与单测零改动。
    """
    result, warnings = split_hair_bangs_strands(part_masks)
    result, more = split_hair_side_strands(result)
    warnings.extend(more)
    result, more = split_limb_segments(result)
    warnings.extend(more)
    result, more = split_leg_segments(result)
    warnings.extend(more)
    result, more = split_skirt_inner(result)
    warnings.extend(more)
    if image_rgb is not None:
        result, more = split_eye_details(result, image_rgb)
        warnings.extend(more)
        result, more = split_mouth_details(result, image_rgb)
        warnings.extend(more)
    return result, warnings


def is_sided_name(name: str) -> bool:
    return _side_suffix(name) is not None


def sided_bases(names: Iterable[str]) -> List[str]:
    bases: List[str] = []
    for name in names:
        lowered = str(name).lower()
        for suffix in ("_left", "_right", "_l", "_r"):
            if lowered.endswith(suffix):
                bases.append(lowered[: -len(suffix)])
                break
    return bases


# ─────────────────── P1 眼部 / 口部细节拆分 ───────────────────
#
# 原则：只从**已归本层所有**的掩码里按像素证据再分，绝不跨层搬像素
# （越界拆分会破坏 INV-3 的拆分守恒）。证据不足时保留整块并写出机器码，
# 由 manifest 标成 split_limited / manual_required，而不是猜着拆。

#: 眼部细节的证据阈值。
EYE_DETAIL_MIN_AREA = 40
EYE_DETAIL_MIN_CONTRAST = 25.0
HIGHLIGHT_MIN_LUMA = 235.0
HIGHLIGHT_MIN_GAP = 55.0
HIGHLIGHT_RATIO_RANGE = (0.002, 0.15)
PUPIL_RATIO_RANGE = (0.08, 0.60)
LASH_MIN_SIDE_RATIO = 0.15

#: 口部细节的证据阈值。闭口线高度不足时整块保留。
MOUTH_DETAIL_MIN_HEIGHT = 10
TEETH_MIN_LUMA = 195.0
TEETH_MAX_SATURATION = 32.0
TEETH_RATIO_RANGE = (0.01, 0.50)
TONGUE_RED_MARGIN = 18.0
TONGUE_RATIO_RANGE = (0.01, 0.40)


def _luminance(rgb: np.ndarray) -> np.ndarray:
    return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1]
            + 0.114 * rgb[..., 2]).astype(np.float32)


def _saturation(rgb: np.ndarray) -> np.ndarray:
    arr = rgb[..., :3].astype(np.float32)
    return (arr.max(axis=-1) - arr.min(axis=-1)).astype(np.float32)


def _otsu_threshold(values: np.ndarray) -> Optional[float]:
    """对一组亮度值做 Otsu 二分，返回阈值；全部同值时返回 ``None``。

    为什么不用 p20/p80 的中点：瞳孔/高光在整块虹膜里往往只占几个百分点，
    分位数会双双落在背景簇上（实测 9% 的瞳芯让 p20==p80），阈值于是退化成
    「整块都算瞳孔」。Otsu 最大化类间方差，对小簇同样成立。
    """
    arr = np.asarray(values, dtype=np.float64).ravel()
    if arr.size < 2:
        return None
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1.0:
        return None
    hist, edges = np.histogram(arr, bins=64, range=(lo, hi))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return None
    p = hist / total
    omega = np.cumsum(p)
    centres = (edges[:-1] + edges[1:]) / 2.0
    mu = np.cumsum(p * centres)
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma_b = np.where(denom > 1e-12, (mu_t * omega - mu) ** 2 / denom, 0.0)
    idx = int(np.argmax(sigma_b))
    return float(centres[idx])


def split_eye_details(
    part_masks: Dict[str, np.ndarray],
    image_rgb: np.ndarray,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """在虹膜内拆瞳孔/高光，在睫毛内拆上下睫毛。

    * 瞳孔：虹膜区域亮度较低的簇（占比须在合理区间，否则判为证据不足）；
    * 高光：虹膜内接近镜面的极小亮簇；
    * 上下睫毛：只对**已存在的** eyelash 掩码按眼睛中心线上下分区。

    任何一步证据不足都保留母层，并写 ``SplitNote`` 说明原因。
    """
    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []
    rgb = np.asarray(image_rgb)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return result, warnings

    lum = _luminance(rgb)

    for side in ("L", "R"):
        iris_key = next((k for k in (f"irides_{side}", f"eye_iris_{side}")
                         if k in result), None)
        if iris_key is None:
            continue
        iris = np.asarray(result[iris_key], dtype=bool)
        area = int(iris.sum())
        if area < EYE_DETAIL_MIN_AREA:
            warnings.append(SplitNote(
                f"{iris_key} 面积 {area}px 不足以拆瞳孔/高光",
                "eye_detail_low_evidence", iris_key))
            continue

        values = lum[iris]
        low = float(np.percentile(values, 2))
        high = float(np.percentile(values, 98))
        if high - low < EYE_DETAIL_MIN_CONTRAST:
            warnings.append(SplitNote(
                f"{iris_key} 虹膜明暗差 {high - low:.0f} 不足，未拆瞳孔/高光",
                "eye_detail_low_evidence", iris_key))
            continue

        lum_iris = float(values.mean())
        # 高光：接近镜面的小亮簇
        highlight = iris & (lum >= HIGHLIGHT_MIN_LUMA)
        hr = float(highlight.sum()) / area
        has_highlight = (
            HIGHLIGHT_RATIO_RANGE[0] <= hr <= HIGHLIGHT_RATIO_RANGE[1]
            and float(values.max()) - lum_iris >= HIGHLIGHT_MIN_GAP
        )
        if not has_highlight:
            highlight = np.zeros_like(iris)

        base = iris & ~highlight
        base_area = int(base.sum())
        # 瞳孔：剩余虹膜用 Otsu 二分（小簇也成立，不依赖分位点）
        if base_area >= EYE_DETAIL_MIN_AREA:
            thr = _otsu_threshold(lum[base])
            if thr is None:
                warnings.append(SplitNote(
                    f"{iris_key} 虹膜亮度单一，未拆瞳孔",
                    "eye_detail_low_evidence", iris_key))
                continue
            pupil = base & (lum <= thr)
            pr = float(pupil.sum()) / max(1, base_area)
            if PUPIL_RATIO_RANGE[0] <= pr <= PUPIL_RATIO_RANGE[1]:
                result.pop(iris_key, None)
                result[f"eye_pupil_{side}"] = pupil
                # 虹膜余量沿用原键名，避免打断下游的 irides_* 绑定。
                result[f"irides_{side}"] = base & ~pupil
                if has_highlight:
                    result[f"eye_highlight_{side}"] = highlight
            else:
                warnings.append(SplitNote(
                    f"{iris_key} 瞳孔占比 {pr:.0%} 异常，保留整块虹膜",
                    "eye_detail_low_evidence", iris_key))

    # 上下睫毛：只在已有 eyelash 掩码上按眼睛中心线分区
    for side in ("L", "R"):
        lash_key = f"eyelash_{side}"
        lash = result.get(lash_key)
        if lash is None:
            continue
        lash = np.asarray(lash, dtype=bool)
        total = int(lash.sum())
        if total == 0:
            continue
        rows = np.where(lash.any(axis=1))[0]
        top, bottom = int(rows.min()), int(rows.max())
        if bottom - top < 3:
            warnings.append(SplitNote(
                f"{lash_key} 只有 {bottom - top + 1}px 高，无法分上下睫毛",
                "lash_not_separable", lash_key))
            continue
        cut = (top + bottom) // 2
        upper = lash.copy()
        upper[cut:, :] = False
        lower = lash.copy()
        lower[:cut, :] = False
        if (int(upper.sum()) < total * LASH_MIN_SIDE_RATIO
                or int(lower.sum()) < total * LASH_MIN_SIDE_RATIO):
            warnings.append(SplitNote(
                f"{lash_key} 上下比例失衡，未拆上下睫毛",
                "lash_not_separable", lash_key))
            continue
        result.pop(lash_key, None)
        result[f"eyelash_upper_{side}"] = upper
        result[f"eyelash_lower_{side}"] = lower

    return result, warnings


def split_mouth_details(
    part_masks: Dict[str, np.ndarray],
    image_rgb: np.ndarray,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """把嘴部分成牙齿 / 舌头 / 上下唇。

    只有**张口证据**（嘴部高度足够且能分出牙齿或舌头）才拆；闭口线一律整块
    保留并写 ``mouth_closed_line``，绝不把一条唇线拆成上下两片假嘴唇。
    """
    result: Dict[str, np.ndarray] = dict(part_masks)
    warnings: List[str] = []
    mouth = result.get("mouth")
    if mouth is None:
        return result, warnings
    mouth = np.asarray(mouth, dtype=bool)
    total = int(mouth.sum())
    if total == 0:
        return result, warnings

    rgb = np.asarray(image_rgb)
    rows = np.where(mouth.any(axis=1))[0]
    height = int(rows.max() - rows.min() + 1) if rows.size else 0
    if height < MOUTH_DETAIL_MIN_HEIGHT:
        warnings.append(SplitNote(
            f"mouth 高度 {height}px（疑似闭口线），未拆唇/齿/舌",
            "mouth_closed_line", "mouth"))
        return result, warnings

    lum = _luminance(rgb)
    sat = _saturation(rgb)

    teeth = mouth & (lum >= TEETH_MIN_LUMA) & (sat <= TEETH_MAX_SATURATION)
    tr = float(teeth.sum()) / total
    has_teeth = TEETH_RATIO_RANGE[0] <= tr <= TEETH_RATIO_RANGE[1]

    red = ((rgb[..., 0].astype(np.float32) - rgb[..., 1].astype(np.float32) > TONGUE_RED_MARGIN)
           & (rgb[..., 0].astype(np.float32) - rgb[..., 2].astype(np.float32) > TONGUE_RED_MARGIN)
           & (lum < TEETH_MIN_LUMA))
    tongue = mouth & red
    if has_teeth:
        tongue = tongue & ~teeth
    gr = float(tongue.sum()) / total
    has_tongue = TONGUE_RATIO_RANGE[0] <= gr <= TONGUE_RATIO_RANGE[1]

    if not has_teeth and not has_tongue:
        warnings.append(SplitNote(
            "mouth 未找到牙齿/舌头证据，未拆口部细节",
            "mouth_detail_low_evidence", "mouth"))
        return result, warnings

    base = mouth.copy()
    if has_teeth:
        base &= ~teeth
    if has_tongue:
        base &= ~tongue
    if not base.any():
        warnings.append(SplitNote(
            "mouth 扣除牙齿/舌头后没有剩余唇部像素，保留整块",
            "mouth_detail_low_evidence", "mouth"))
        return result, warnings

    base_rows = np.where(base.any(axis=1))[0]
    cut = (int(base_rows.min()) + int(base_rows.max())) // 2
    upper = base.copy()
    upper[cut:, :] = False
    lower = base.copy()
    lower[:cut, :] = False

    result.pop("mouth", None)
    if has_teeth:
        result["teeth"] = teeth
    if has_tongue:
        result["tongue"] = tongue
    if upper.any():
        result["lip_upper"] = upper
    if lower.any():
        result["lip_lower"] = lower
    return result, warnings
