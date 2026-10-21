#!/usr/bin/env python3
"""
Ground-truth-free quality score for a set of Live2D part layers.

Why this exists: mean mask coverage ranks parts by *size*, not by correctness.
Measured on docs/assets/demo_input.png, the seven smallest parts (eyes, nose,
brows, hands) are visually correct while ``face`` (coverage 0.0084) fills only
61% of its own box and leaves 22% of the eyebrows outside it — so tuning
against coverage would "fix" the number by breaking the parts that already
work.

Every check is an internal relation that must hold between parts no matter what
the character looks like, so this runs without labels and without torch (pure
numpy/cv2, ~1s) and can sit inside a parameter sweep or the workflow itself.

The negative controls live here too (:func:`selftest`): a defect count means
nothing unless the checks are demonstrably capable of firing, and a clean set
must produce zero defects or the metric is just a machine that complains.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
import cv2

# Parts that must come back non-empty for a standing character.
#
# ``mouth`` is required here but NOT in tools/verify_sam2_gd.py's list: that
# tool checks "did the backend separate anything", where an illustration may
# legitimately hide a feature. This module gates the *product*, and a Live2D
# pet with no mouth layer has no mouth motion at all — measured, the production
# defaults miss the mouth on 4 of 5 test inputs, and the face-crop pass is what
# recovers it (mouth_in_face 1.00).
REQUIRED_PARTS = (
    "hair_back", "hair_front", "face", "eyes_left", "eyes_right", "mouth",
    "neck", "clothes_top",
)

#: Features that live on the face: their pixels must be inside the face mask.
FEATURES_IN_FACE = ("eyebrows", "eyes_left", "eyes_right", "nose", "mouth")

#: Top-to-bottom body order. Two parts both present must respect it, which is
#: what catches a "mouth" box that actually landed on the character's knees.
VERTICAL_CHAIN = (
    "eyes_left", "nose", "mouth", "neck", "clothes_top", "clothes_bottom",
    "legs",
)

CONTAIN_MIN = 0.80      # feature-inside-face fraction
DUP_CONTAINMENT = 0.60  # smaller mask swallowed by a bigger one => duplicate
IOU_MAX = 0.25          # pairwise overlap ceiling
LARGEST_CC_MIN = 0.60   # anti-fragmentation share of one part's own pixels
FRAGMENT_MIN_PIXELS = 200  # parts below this are single dots by nature (a nose)
MAX_BLOBS = 2           # hands/legs are two blobs each; more is shattering
LEAK_MAX = 0.05         # part pixels allowed outside the character silhouette
EXPLAINED_MIN = 0.70    # silhouette area that must be claimed by some part

#: Gate for the workflow. Measured spread over five inputs with the production
#: defaults: 2 defects on docs/assets/demo_input.png, 7/9/13/15 on the four
#: generated characters in output/. 4 sits between the good case and every
#: observed failure rather than being picked from thin air.
MAX_DEFECTS = 4


def load_masks(layers_dir: Path) -> Dict[str, np.ndarray]:
    """Every ``<part>.png`` in *layers_dir* as a boolean mask (alpha > 0)."""
    masks: Dict[str, np.ndarray] = {}
    for png in sorted(Path(layers_dir).glob("*.png")):
        if png.stem in ("preview", "composite_preview"):
            continue
        alpha = np.asarray(Image.open(png).convert("RGBA"))[..., 3]
        masks[png.stem] = alpha > 0
    return masks


def foreground_of(image: Image.Image) -> Optional[np.ndarray]:
    """The character silhouette, when the image actually carries one.

    The pipeline segments the *optimized* image (background removed), so in
    production that alpha is real and makes the leak / unexplained checks
    truth-backed. A flat-alpha input yields ``None`` and those checks stay off
    rather than firing on a silhouette this module invented.
    """
    if "A" not in image.getbands():
        return None
    alpha = np.asarray(image.convert("RGBA"))[..., 3]
    frac = float((alpha > 10).mean())
    if frac <= 0.0 or frac >= 0.999:
        return None
    return alpha > 10


def _bbox(mask: np.ndarray) -> Optional[List[int]]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _components(mask: np.ndarray) -> Tuple[int, float]:
    """(connected component count, largest component / total pixels)."""
    if not mask.any():
        return 0, 0.0
    n, _, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    if n <= 1:
        return 0, 0.0
    areas = stats[1:, cv2.CC_STAT_AREA]
    return int(len(areas)), float(areas.max() / areas.sum())


def _centroid(mask: np.ndarray) -> Optional[float]:
    ys, _ = np.nonzero(mask)
    return float(ys.mean()) if len(ys) else None


def per_part(mask: np.ndarray, total_pixels: int) -> Dict[str, float]:
    cc, largest = _components(mask)
    return {
        "pixel_count": int(mask.sum()),
        "coverage": float(mask.sum() / total_pixels),
        "components": cc,
        "largest_cc_share": largest,
        "bbox": _bbox(mask) or [],
        "centroid_y": _centroid(mask),
    }


def _containment(a: np.ndarray, b: np.ndarray) -> float:
    """Fraction of *a* that sits inside *b*."""
    denom = int(a.sum())
    return float((a & b).sum() / denom) if denom else 0.0


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int((a | b).sum())
    return float((a & b).sum() / union) if union else 0.0


def _canonical(name: str) -> Tuple[str, Optional[str]]:
    """把层名归一到 ``layer_manifest`` 的规范部件名（含侧别）。"""
    from core.segment_engine.layer_manifest import resolve_part

    _pid, base, side = resolve_part(name)
    return base, side


def canonical_index(
    masks: Dict[str, np.ndarray],
) -> Dict[Tuple[str, Optional[str]], np.ndarray]:
    """按规范部件名聚合掩码，同名的拆分层直接并起来。

    合成器会把 ``eyes_left`` 拆成 ``eyewhite_left`` + ``irides_left``，
    把 ``legs`` 拆成 ``leg_L``/``leg_R``。质量门如果直接拿原始层名比对，
    就会把**已经分好的**眼睛和腿报成缺失——实测 demo 输入上白报 2 条缺陷，
    正好把 defect_count 顶到 ``MAX_DEFECTS``，等于一台只会误报的机器。
    """
    index: Dict[Tuple[str, Optional[str]], np.ndarray] = {}
    for name, mask in masks.items():
        key = _canonical(name)
        if key[0] == "unknown":
            # 认不出的层名不参与归并：否则两个不相干的未知层会撞到同一个
            # ("unknown", side) 键上被并成一块，反而制造出假部件。
            continue
        index[key] = (index[key] | mask) if key in index else mask
    return index


def has_part(
    index: Dict[Tuple[str, Optional[str]], np.ndarray], part: str
) -> bool:
    base, side = _canonical(part)
    if side is not None:
        # 问「左眼有没有」：分侧的层命中，或者没拆分时的 "eyes" 也算
        return (base, side) in index or (base, None) in index
    # 问「眼睛/腿有没有」：任意一侧存在就算有
    if (base, None) in index:
        return True
    return any(existing == base for existing, _side in index)


def part_mask(
    index: Dict[Tuple[str, Optional[str]], np.ndarray], part: str
) -> Optional[np.ndarray]:
    base, side = _canonical(part)
    if (base, side) in index:
        return index[(base, side)]
    return index.get((base, None))


def check_features_in_face(masks: Dict[str, np.ndarray]) -> List[str]:
    index = canonical_index(masks)
    face = part_mask(index, "face")
    if face is None:
        return ["face 掩码缺失，无法校验五官归属"]
    bad = []
    for part in FEATURES_IN_FACE:
        m = part_mask(index, part)
        if m is None:
            continue
        frac = _containment(m, face)
        if frac < CONTAIN_MIN:
            bad.append(
                f"{part} 只有 {frac:.2f} 的像素落在 face 掩码内（下限 {CONTAIN_MIN}）"
                "——face 掩码没有覆盖到该五官"
            )
    return bad


def _amodal_parts() -> set:
    """Layers the composer deliberately grows into their occluded region."""
    from core.segment_engine.composer import LayerComposer
    return set(LayerComposer.AMODAL_PARTS)


def _synthesized_overlay_sources() -> Dict[str, set]:
    """合成层 → 它取自的层（闭眼图取自眼球层、口内取自嘴线层）。

    这些层是**故意**与源层 100% 重叠的：闭眼图就是眼睛那块面积填了皮肤，口内
    就是嘴线那块面积填了暖色。质量门按"谁盖住谁"判缺陷，认不出这条关系就会把
    设计报成越界扩张（实测 5 条，占满 MAX_DEFECTS 门槛，把整轮分层判为不合格）。
    """
    from core.segment_engine.composer import LayerComposer
    return {
        name: set(sources)
        for name, sources in LayerComposer.synthesized_overlay_sources().items()
    }


def _rest_invisible_parts() -> set:
    """静止时**不画出来**的合成层（腮红 / 眼泪，靠不透明度键形淡入）。

    它们与任何层的重叠都不是"越界扩张"：静止姿态下它们根本不存在，运行时的
    可见性完全由参数控制。所以两两重叠/包含检查都要绕过它们。
    """
    from core.segment_engine.composer import LayerComposer
    return set(LayerComposer.REST_INVISIBLE_PARTS)


def _is_synthesized_overlay_pair(overlays: Dict[str, set],
                                 small: str, large: str) -> bool:
    """``small``/``large`` 是不是「合成层 ↔ 它取自的层」这一对。

    与面积无关：合成层可能比源层大（闭眼图填了整块眼睛区域），也可能更小
    （抗锯齿边缘被切掉），所以两个方向都要认。
    """
    return (small in overlays.get(large, ())
            or large in overlays.get(small, ()))


def check_duplicates(masks: Dict[str, np.ndarray]) -> Tuple[List[str], List[str]]:
    """A layer swallowed whole by another one.

    An amodal layer (hair_back, clothes_top, neck, ...) is *supposed* to grow
    behind the parts in front of it, so it containing another part is design,
    not a defect — those go to ``notes``. A non-amodal layer covering another
    part is a real over-extension. The same holds for a **synthesized overlay**
    (closed-eye image, mouth cavity) against the layers it was built from.
    """
    amodal = _amodal_parts()
    overlays = _synthesized_overlay_sources()
    hidden = _rest_invisible_parts()
    names = sorted(masks)
    bad: List[str] = []
    notes: List[str] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ma, mb = masks[a], masks[b]
            sa, sb = int(ma.sum()), int(mb.sum())
            if not sa or not sb:
                continue
            small, large = (a, b) if sa <= sb else (b, a)
            inter = int((ma & mb).sum())
            dup = inter / min(sa, sb)
            iou = _iou(ma, mb)
            if dup <= DUP_CONTAINMENT or iou <= IOU_MAX:
                continue
            if small in hidden or large in hidden:
                continue
            if large in amodal:
                notes.append(
                    f"{small} 落在 amodal 层 {large} 内（{dup:.2f}，IoU {iou:.2f}）"
                    "——补全设计如此"
                )
            elif _is_synthesized_overlay_pair(overlays, small, large):
                notes.append(
                    f"{small} 落在合成层 {large} 内（{dup:.2f}，IoU {iou:.2f}）"
                    "——合成层就是照着它造的"
                )
            else:
                bad.append(
                    f"{small} 的 {dup:.2f} 面积被非 amodal 层 {large} 覆盖"
                    f"（IoU {iou:.2f}）——{large} 越界扩张，合成时会盖掉 {small}"
                )
    return bad, notes


def check_overlap(masks: Dict[str, np.ndarray]) -> List[str]:
    """Pairwise IoU that is high but not a duplicate (partial collisions)."""
    hidden = _rest_invisible_parts()
    names = sorted(masks)
    bad = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a in hidden or b in hidden:
                # 静止时不画的层不参与两两重叠判定（见 _rest_invisible_parts）
                continue
            ma, mb = masks[a], masks[b]
            smaller = min(int(ma.sum()), int(mb.sum()))
            if not smaller:
                continue
            iou = _iou(ma, mb)
            dup = (ma & mb).sum() / smaller
            if iou > IOU_MAX and dup <= DUP_CONTAINMENT:
                bad.append(f"{a} ∩ {b} IoU {iou:.2f} 超过上限 {IOU_MAX}")
    return bad


def check_fragmentation(masks: Dict[str, np.ndarray]) -> List[str]:
    """A part shattered into many comparable specks.

    ``> MAX_BLOBS`` is part of the test on purpose: two hands and two legs are
    two connected components each and are not fragmentation.
    """
    bad = []
    for part, m in sorted(masks.items()):
        if int(m.sum()) < FRAGMENT_MIN_PIXELS:
            continue
        n, largest = _components(m)
        if n > MAX_BLOBS and largest < LARGEST_CC_MIN:
            bad.append(
                f"{part} 碎成 {n} 块且最大块只占自身 {largest:.2f}"
                f"（下限 {LARGEST_CC_MIN}）"
            )
    return bad


def check_vertical_order(masks: Dict[str, np.ndarray]) -> List[str]:
    present = [p for p in VERTICAL_CHAIN if p in masks]
    bad = []
    for a, b in zip(present, present[1:]):
        ya, yb = _centroid(masks[a]), _centroid(masks[b])
        if ya is None or yb is None:
            continue
        if ya >= yb:
            bad.append(
                f"{a} 的重心 y={ya:.0f} 不在 {b} (y={yb:.0f}) 上方"
                "——身体顺序反了，多半是某一层框错了位置"
            )
    return bad


def check_required(masks: Dict[str, np.ndarray],
                   require: Tuple[str, ...] = REQUIRED_PARTS) -> List[str]:
    index = canonical_index(masks)
    return [f"必需部件 {p} 没有掩码" for p in require if not has_part(index, p)]


def missing_standard(masks: Dict[str, np.ndarray]) -> List[str]:
    """Parts the prompt table can name but this run did not emit.

    Reported, not failed: an illustration may genuinely hide hands or a mouth.
    """
    from core.segment_engine.semantic import SemanticSegmenter
    index = canonical_index(masks)
    return [p for p in SemanticSegmenter.STANDARD_PARTS if not has_part(index, p)]


CHECKS = (
    ("features_in_face", check_features_in_face),
    ("pairwise_overlap", check_overlap),
    ("fragmentation", check_fragmentation),
    ("vertical_order", check_vertical_order),
)


def check_silhouette_leak(masks: Dict[str, np.ndarray],
                          fg: np.ndarray) -> List[str]:
    """Masks must stay on the character. Only possible with a real silhouette
    (the pipeline's cutout gives one for free)."""
    bad = []
    for part, m in sorted(masks.items()):
        pixels = int(m.sum())
        if not pixels:
            continue
        leak = int((m & ~fg).sum()) / pixels
        if leak > LEAK_MAX:
            bad.append(
                f"{part} 有 {leak:.2f} 的像素在角色轮廓外（上限 {LEAK_MAX}）"
            )
    return bad


def check_unexplained(masks: Dict[str, np.ndarray],
                      fg: np.ndarray) -> List[str]:
    """Most of the character must be claimed by some part."""
    union = np.logical_or.reduce([m for m in masks.values()])
    frac = float((union & fg).sum()) / max(1, int(fg.sum()))
    if frac < EXPLAINED_MIN:
        return [f"部件只解释了角色面积的 {frac:.2f}（下限 {EXPLAINED_MIN}）"]
    return []


def explained_fraction(masks: Dict[str, np.ndarray],
                       fg: np.ndarray) -> float:
    union = np.logical_or.reduce([m for m in masks.values()])
    return float((union & fg).sum()) / max(1, int(fg.sum()))


def score(layers_dir: Path,
          require: Tuple[str, ...] = REQUIRED_PARTS,
          foreground: Optional[np.ndarray] = None) -> Dict[str, object]:
    masks = load_masks(layers_dir)
    if not masks:
        return {"error": f"no layer PNGs in {layers_dir}"}
    if foreground is not None:
        shape = next(iter(masks.values())).shape
        if tuple(foreground.shape) != tuple(shape):
            return {"error": f"轮廓图尺寸 {foreground.shape} 与图层 {shape} 不一致"}
    return score_masks(masks, require, foreground)


def score_masks(masks: Dict[str, np.ndarray],
                require: Tuple[str, ...] = REQUIRED_PARTS,
                foreground: Optional[np.ndarray] = None) -> Dict[str, object]:
    """The checks, applied to an in-memory mask set (see :func:`selftest`)."""
    total = int(next(iter(masks.values())).size)
    defects: Dict[str, List[str]] = {}
    required_missing = check_required(masks, require)
    if required_missing:
        defects["required_parts"] = required_missing
    for name, fn in CHECKS:
        found = fn(masks)
        if found:
            defects[name] = found
    dup_defects, amodal_notes = check_duplicates(masks)
    if dup_defects:
        defects["duplicate_layers"] = dup_defects
    if foreground is not None:
        leak = check_silhouette_leak(masks, foreground)
        if leak:
            defects["silhouette_leak"] = leak
        unexplained = check_unexplained(masks, foreground)
        if unexplained:
            defects["unexplained_foreground"] = unexplained
    return {
        "part_count": len(masks),
        "total_pixels": total,
        "parts": {p: per_part(m, total) for p, m in sorted(masks.items())},
        "defects": defects,
        "defect_count": sum(len(v) for v in defects.values()),
        "missing_parts": missing_standard(masks),
        "amodal_notes": amodal_notes,
        "union_coverage": float(
            np.logical_or.reduce([m for m in masks.values()]).sum() / total
        ),
        "explained_fraction": (
            round(explained_fraction(masks, foreground), 4)
            if foreground is not None else None
        ),
    }


def verdict(result: Dict[str, object],
            max_defects: int = MAX_DEFECTS) -> Dict[str, object]:
    """Pass/fail for the workflow gate, with the reasons attached.

    ``explained_fraction`` is only checked when a real silhouette was available
    (``None`` means the input had no alpha, not that coverage was zero).
    """
    reasons: List[str] = []
    count = int(result.get("defect_count") or 0)
    if count > max_defects:
        reasons.append(f"一致性缺陷 {count} 条，超过门槛 {max_defects}")
    explained = result.get("explained_fraction")
    if explained is not None and float(explained) < EXPLAINED_MIN:
        reasons.append(f"角色面积只被解释 {explained:.2f}，低于 {EXPLAINED_MIN}")
    return {"ok": not reasons, "reasons": reasons,
            "defect_count": count, "max_defects": max_defects}


def selftest() -> int:
    """Negative control: prove every check can actually fire.

    A "defects=2" reading only means something if the other checks are capable
    of reporting. Each mutation breaks exactly one relation and must be caught
    by the check that owns it; the clean set must produce zero defects, which
    is what stops the checks from being a machine that always complains.
    """
    h = w = 200

    def rect(x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
        m = np.zeros((h, w), dtype=bool)
        m[y0:y1, x0:x1] = True
        return m

    def clean() -> Dict[str, np.ndarray]:
        return {
            "hair_back": rect(20, 10, 180, 120),
            "hair_front": rect(60, 10, 140, 55),
            "face": rect(70, 30, 130, 100),
            "eyebrows": rect(75, 40, 125, 48),
            "eyes_left": rect(78, 50, 96, 62),
            "eyes_right": rect(104, 50, 122, 62),
            "nose": rect(95, 66, 105, 78),
            "mouth": rect(85, 82, 115, 94),
            "neck": rect(88, 100, 112, 118),
            "clothes_top": rect(60, 118, 140, 160),
            "clothes_bottom": rect(65, 150, 135, 185),
            "arms": rect(40, 120, 60, 165),
            "hands": rect(38, 165, 52, 180),
            "legs": rect(80, 185, 120, 199),
            "accessories": rect(95, 120, 105, 150),
        }

    fg = rect(20, 10, 180, 200)   # the character silhouette

    cases = (
        ("features_in_face", "face 缩小到不含眼睛",
         lambda m: m.__setitem__("face", rect(70, 80, 130, 100)), False),
        ("duplicate_layers", "arms 吞掉整条裙子",
         lambda m: m.__setitem__("arms", m["clothes_bottom"].copy()), False),
        ("fragmentation", "hair_back 碎成 6 块",
         lambda m: m.__setitem__("hair_back", np.logical_or.reduce(
             [rect(20 + 25 * i, 10, 30 + 25 * i, 120) for i in range(6)])),
         False),
        ("vertical_order", "mouth 跑到眼睛上方",
         lambda m: m.__setitem__("mouth", rect(85, 20, 115, 30)), False),
        ("required_parts", "删掉 neck",
         lambda m: m.pop("neck"), False),
        ("silhouette_leak", "legs 画到角色轮廓外",
         lambda m: m.__setitem__("legs", rect(185, 150, 199, 195)), True),
        ("unexplained_foreground", "只剩头发，身体无人认领",
         lambda m: (m.clear(),
                    m.__setitem__("hair_back", rect(20, 10, 180, 60))), True),
    )

    failed = 0
    base = score_masks(clean(), foreground=fg)
    if base["defect_count"]:
        print(f"[selftest FAIL] 干净图层集报了 {base['defect_count']} 条: "
              f"{base['defects']}")
        failed += 1
    else:
        print("[selftest ok ] 干净集 0 缺陷（无系统性误报）")

    for group, label, mutate, use_fg in cases:
        masks = clean()
        mutate(masks)
        result = score_masks(masks, foreground=fg) if use_fg else score_masks(masks)
        fired = group in result["defects"]
        other = {k: v for k, v in result["defects"].items() if k != group}
        if fired:
            print(f"[selftest ok ] {label} -> {group} 命中"
                  + (f"（另触发 {sorted(other)}）" if other else ""))
        else:
            print(f"[selftest FAIL] {label} 未被 {group} 捕获: "
                  f"{result['defects'] or '无缺陷'}")
            failed += 1

    # 正向对照：合成器会把 eyes_left/eyes_right 拆成 eyewhite_*，把 legs 拆成
    # leg_L/leg_R。归一后不能再报「必需部件缺失」，否则正常输出会被误判。
    split = clean()
    split.pop("eyes_left")
    split.pop("eyes_right")
    split.pop("legs")
    split["eyewhite_left"] = rect(78, 50, 96, 62)
    split["eyewhite_right"] = rect(104, 50, 122, 62)
    split["leg_L"] = rect(80, 185, 100, 199)
    split["leg_R"] = rect(100, 185, 120, 199)
    split_result = score_masks(split, foreground=fg)
    if "required_parts" in split_result["defects"] or split_result["missing_parts"]:
        print("[selftest FAIL] 拆分层名（eyewhite_*/leg_*）仍被报成缺失: "
              f"{split_result['defects']} / {split_result['missing_parts']}")
        failed += 1
    else:
        print("[selftest ok ] 拆分层名（eyewhite_*/leg_*）被正确归一到必需部件")

    print(f"\n[selftest] {'全部通过' if not failed else f'{failed} 项未通过'}")
    return 1 if failed else 0
