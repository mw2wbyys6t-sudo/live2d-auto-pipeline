#!/usr/bin/env python3
"""
Pygmalion Studio - Layer Manifest v1

Defines the canonical contract for layered outputs. Every layerer must emit a
``layer_manifest.json`` next to its PNG files so downstream consumers (PSD
export, Live2D builder, preview) read one structure instead of guessing from
file names.

The contract separates two quality levels:

``preview_ready``
    Coarse parts are present; the result is usable for visual inspection only.

``rig_ready``
    Every part needed for left/right-independent rigging is present, with an
    explicit side. Color clustering can never satisfy this level, because it
    cannot tell a left sleeve from a right sleeve.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.segment_engine.part_split import DERIVED_PARTS

SCHEMA_VERSION = "1.1"

MANIFEST_FILENAME = "layer_manifest.json"

#: 分层来源。``source`` 必须**如实反映**实际使用的分层方法，禁止硬编码成
#: ``semantic``——历史上就是这么掩盖了「模型没加载成功、悄悄退化成颜色启发式」
#: 的事实，让用户以为拿到的是可用分层。
SOURCE_SEMANTIC = "semantic"
SOURCE_COLOR_CLUSTER = "color_cluster"
#: 学习式前景剪影（ONNX 抠图模型）+ 颜色启发式部件拆分。剪影质量高，但部件
#: 边界仍由颜色规则切出，不等价于部位级学习分割。
SOURCE_SEMANTIC_FG_GATED = "semantic_fg_gated_color"
#: 纯颜色启发式兜底：连前景剪影都没有，属于严重降级。
SOURCE_SEMANTIC_HSV_FALLBACK = "semantic_hsv_fallback"
#: 用户自定义分割模型。
SOURCE_CUSTOM = "custom_segmentation"

#: 允许支撑 Live2D 左右独立绑定的来源。其余来源只能用于预览。
_RIG_CAPABLE_SOURCES = frozenset({
    SOURCE_SEMANTIC,
    SOURCE_SEMANTIC_FG_GATED,
    SOURCE_CUSTOM,
})

#: 各来源面向用户的中文说明。
SOURCE_NOTES: Dict[str, str] = {
    SOURCE_SEMANTIC: "部位级语义分割",
    SOURCE_COLOR_CLUSTER: "颜色聚类，仅可预览",
    SOURCE_SEMANTIC_FG_GATED: "学习式前景剪影 + 颜色启发式部件拆分",
    SOURCE_SEMANTIC_HSV_FALLBACK: "纯颜色启发式兜底（未获得前景模型）",
    SOURCE_CUSTOM: "自定义分割模型",
}

#: 分层器返回的 ``method`` -> manifest ``source``。
_METHOD_TO_SOURCE: Dict[str, str] = {
    "kmeans": SOURCE_COLOR_CLUSTER,
    "k-means": SOURCE_COLOR_CLUSTER,
    "color": SOURCE_COLOR_CLUSTER,
    "color_cluster": SOURCE_COLOR_CLUSTER,
    "semantic": SOURCE_SEMANTIC,
    # l2d_sam_iter2 是部位级学习分割，不能因为名字没登记就落进「纯颜色启发式
    # 兜底」——那会让 layer_manifest.json 把真实模型结果标成严重降级。
    "l2d_sam_iter2": SOURCE_SEMANTIC,
    "l2d_sam": SOURCE_SEMANTIC,
    "semantic_fg_gated_color": SOURCE_SEMANTIC_FG_GATED,
    "semantic_hsv_fallback": SOURCE_SEMANTIC_HSV_FALLBACK,
    "custom_segmentation": SOURCE_CUSTOM,
}


def source_for_method(method: str) -> str:
    """把分层器的 ``method`` 映射为 manifest 的 ``source``。

    未知方法按最保守的方式处理（视为纯颜色兜底），避免把不明状态粉饰成
    高质量语义分层。
    """
    key = str(method or "").strip().lower()
    if key in _METHOD_TO_SOURCE:
        return _METHOD_TO_SOURCE[key]
    if key.startswith("custom"):
        return SOURCE_CUSTOM
    if key.startswith("semantic"):
        return SOURCE_SEMANTIC
    return SOURCE_SEMANTIC_HSV_FALLBACK

GROUP_ORDER: List[Dict[str, str]] = [
    {"id": "head_hair", "psd_name": "01_head_hair", "name_zh": "01_头部·头发", "order": 1},
    {"id": "head_face", "psd_name": "02_head_face", "name_zh": "01_头部·面部", "order": 2},
    {"id": "body", "psd_name": "03_body", "name_zh": "02_身体", "order": 3},
    {"id": "clothing", "psd_name": "04_clothing", "name_zh": "03_服装", "order": 4},
    {"id": "lower", "psd_name": "05_lower", "name_zh": "04_下半身", "order": 5},
    {"id": "accessory", "psd_name": "06_accessory", "name_zh": "05_配件", "order": 6},
]

_GROUP_IDS = {g["id"] for g in GROUP_ORDER}

PART_SPECS: Dict[str, Dict[str, Any]] = {
    "hair_back": {"name_zh": "头发_后", "group": "head_hair", "z": 10, "sides": True},
    "hair_bangs": {"name_zh": "头发_刘海", "group": "head_hair", "z": 60, "sides": False},
    "hair_side": {"name_zh": "头发_鬓发", "group": "head_hair", "z": 55, "sides": True},
    "hair_braid": {"name_zh": "头发_辫子", "group": "head_hair", "z": 12, "sides": True},
    "hair_ornament": {"name_zh": "头发_发饰", "group": "head_hair", "z": 65, "sides": False},
    "ribbon": {"name_zh": "头发_丝带", "group": "head_hair", "z": 66, "sides": True},
    "face_base": {"name_zh": "面部_脸", "group": "head_face", "z": 40, "sides": False},
    "cheek": {"name_zh": "面部_脸颊", "group": "head_face", "z": 41, "sides": False},
    "eyebrow": {"name_zh": "面部_眉毛", "group": "head_face", "z": 44, "sides": True},
    "eye_white": {"name_zh": "面部_眼白", "group": "head_face", "z": 45, "sides": True},
    "eye_iris": {"name_zh": "面部_瞳孔", "group": "head_face", "z": 46, "sides": True},
    # P1 眼细节（split_eye_details）：由虹膜/睫毛掩码按像素证据再分。
    "eye_pupil": {"name_zh": "面部_瞳芯", "group": "head_face", "z": 46, "sides": True},
    "eye_highlight": {"name_zh": "面部_眼高光", "group": "head_face", "z": 47, "sides": True},
    "eyelash": {"name_zh": "面部_睫毛", "group": "head_face", "z": 47, "sides": True},
    "eyelash_upper": {"name_zh": "面部_上睫毛", "group": "head_face", "z": 48, "sides": True},
    "eyelash_lower": {"name_zh": "面部_下睫毛", "group": "head_face", "z": 47, "sides": True},
    "nose": {"name_zh": "面部_鼻", "group": "head_face", "z": 42, "sides": False},
    "mouth": {"name_zh": "面部_嘴", "group": "head_face", "z": 43, "sides": False},
    # P1 口部细节（split_mouth_details）：只在张口证据充分时由 mouth 再分。
    "teeth": {"name_zh": "面部_牙齿", "group": "head_face", "z": 43, "sides": False},
    "tongue": {"name_zh": "面部_舌头", "group": "head_face", "z": 42, "sides": False},
    "lip_upper": {"name_zh": "面部_上唇", "group": "head_face", "z": 44, "sides": False},
    "lip_lower": {"name_zh": "面部_下唇", "group": "head_face", "z": 43, "sides": False},
    "neck": {"name_zh": "身体_脖子", "group": "body", "z": 30, "sides": False},
    "neck_shadow": {"name_zh": "身体_颈部阴影", "group": "body", "z": 31, "sides": False},
    "torso": {"name_zh": "身体_躯干", "group": "body", "z": 32, "sides": False},
    "arm": {"name_zh": "身体_手臂", "group": "body", "z": 33, "sides": True},
    "arm_upper": {"name_zh": "身体_上臂", "group": "body", "z": 33, "sides": True},
    "forearm": {"name_zh": "身体_前臂", "group": "body", "z": 34, "sides": True},
    "arm_lower": {"name_zh": "身体_小臂", "group": "body", "z": 34, "sides": True},
    "hand": {"name_zh": "身体_手", "group": "body", "z": 35, "sides": True},
    "waist": {"name_zh": "身体_腰带", "group": "body", "z": 36, "sides": False},
    "clothes_top": {"name_zh": "服装_上装", "group": "clothing", "z": 50, "sides": False},
    "outer_body": {"name_zh": "服装_外套主体", "group": "clothing", "z": 51, "sides": False},
    "outer_collar": {"name_zh": "服装_外套领口", "group": "clothing", "z": 52, "sides": False},
    "sleeve": {"name_zh": "服装_袖子", "group": "clothing", "z": 53, "sides": True},
    "cuff": {"name_zh": "服装_袖口", "group": "clothing", "z": 54, "sides": True},
    "collar": {"name_zh": "服装_领口", "group": "clothing", "z": 57, "sides": False},
    "skirt_hem": {"name_zh": "服装_裙摆", "group": "clothing", "z": 49, "sides": True},
    "waistband": {"name_zh": "服装_腰带", "group": "clothing", "z": 50, "sides": False},
    "shirt_collar": {"name_zh": "服装_衬衫领口", "group": "clothing", "z": 54, "sides": False},
    "shirt_fold": {"name_zh": "服装_衬衫褶皱", "group": "clothing", "z": 55, "sides": False},
    "brooch": {"name_zh": "服装_胸针", "group": "clothing", "z": 56, "sides": False},
    "clothes_bottom": {"name_zh": "服装_下装", "group": "clothing", "z": 49, "sides": False},
    "skirt_body": {"name_zh": "服装_裙主体", "group": "clothing", "z": 48, "sides": False},
    # 裙内层由 split_skirt_inner 按内外轮廓腐蚀拆出，画在外轮廓之后（z 更小 = 更靠后）。
    "skirt_inner": {"name_zh": "服装_裙内层", "group": "clothing", "z": 46, "sides": False},
    "skirt_front": {"name_zh": "服装_裙摆_前", "group": "clothing", "z": 47, "sides": False},
    "skirt_side": {"name_zh": "服装_裙摆_侧", "group": "clothing", "z": 46, "sides": True},
    "skirt_back": {"name_zh": "服装_裙摆_后", "group": "clothing", "z": 20, "sides": False},
    "bottomwear_front": {"name_zh": "服装_下装_前", "group": "clothing", "z": 47, "sides": False},
    "bottomwear_back": {"name_zh": "服装_下装_后", "group": "clothing", "z": 19, "sides": False},
    "lining": {"name_zh": "服装_内衬", "group": "clothing", "z": 45, "sides": False},
    "sock": {"name_zh": "下半身_袜", "group": "lower", "z": 15, "sides": True},
    "boot": {"name_zh": "下半身_靴", "group": "lower", "z": 16, "sides": True},
    "boot_cuff": {"name_zh": "下半身_靴口", "group": "lower", "z": 17, "sides": True},
    "leg": {"name_zh": "下半身_腿", "group": "lower", "z": 14, "sides": True},
    # 膝盖分段（split_leg_segments）：整腿太矮/过碎时不拆，保留 leg。
    "thigh": {"name_zh": "下半身_大腿", "group": "lower", "z": 14, "sides": True},
    "calf": {"name_zh": "下半身_小腿", "group": "lower", "z": 13, "sides": True},
    "watch": {"name_zh": "配件_怀表", "group": "accessory", "z": 70, "sides": False},
    "pendant": {"name_zh": "配件_挂饰", "group": "accessory", "z": 71, "sides": False},
    "accessories": {"name_zh": "配件_未分类", "group": "accessory", "z": 69, "sides": False},
    "headwear": {"name_zh": "头部_头饰", "group": "head_hair", "z": 68, "sides": False},
    "eyewear": {"name_zh": "面部_眼镜", "group": "head_face", "z": 48, "sides": False},
    "ear": {"name_zh": "面部_耳", "group": "head_face", "z": 39, "sides": True},
    "earwear": {"name_zh": "面部_耳饰", "group": "head_face", "z": 49, "sides": True},
    "handwear": {"name_zh": "身体_手部服饰", "group": "body", "z": 36, "sides": True},
    "tail": {"name_zh": "配件_尾巴", "group": "accessory", "z": 8, "sides": False},
    "wings": {"name_zh": "配件_翅膀", "group": "accessory", "z": 5, "sides": False},
    "objects": {"name_zh": "配件_道具", "group": "accessory", "z": 72, "sides": False},
    # 合成层此前没有登记：它们在清单里被判成「未识别部件」，还被塞进 accessory
    # 组（z=999）→ PSD 里被画到最上层，闭眼图盖住眼球、口内盖住嘴唇。
    # 下面三条把合成层登记到头面组，绘制位置与权威表一致。
    "eyelid": {"name_zh": "面部_闭眼图", "group": "head_face", "z": 43, "sides": True},
    "tear": {"name_zh": "面部_眼泪", "group": "head_face", "z": 47, "sides": True},
    "mouth_cavity": {"name_zh": "面部_口内", "group": "head_face", "z": 42, "sides": False},
}

#: canonical 部件 → 权威表（``composer.STANDARD_LAYER_ORDER``）里的代表层名。
#:
#: 清单的静态绘制顺序必须与权威表一致（ADR-003：层序的唯一权威），否则 PSD
#: 写出的层序与 moc3 的渲染层序对不上，FF-2 的 1:1 契约随即失效。
#: 这里把每个 canonical 部件映射到权威表里的对应层名，用它在权威表中的位置
#: 作为绘制序；映射不到的部件退回各自 spec 里的 z（只影响组内相对顺序）。
STANDARD_ORDER_NAMES: Dict[str, str] = {
    "hair_back": "hair_back",
    "hair_bangs": "hair_bangs",
    "headwear": "headwear",
    "face_base": "face_base",
    "cheek": "cheek",
    "ear": "ears",
    "eyebrow": "eyebrow_L",
    "eye_white": "eyewhite_left",
    "eye_iris": "irides_left",
    "eyelash": "eyelash_left",
    "eyelid": "eyelid_left",
    "tear": "tear_left",
    "eyewear": "eyewear",
    "earwear": "earwear",
    "nose": "nose",
    "mouth": "mouth",
    "mouth_cavity": "mouth_cavity",
    "neck": "neck",
    "arm": "arm_L",
    "forearm": "arm_lower_L",
    "hand": "hand_L",
    "clothes_top": "clothes_top",
    "clothes_inner": "clothes_inner",
    "lining": "clothes_inner",
    "waistband": "waistband",
    "collar": "collar",
    "neckwear": "neckwear",
    "sleeve": "sleeve_L",
    "cuff": "cuff_L",
    "handwear": "handwear",
    "skirt_body": "skirt",
    "skirt_hem": "skirt_hem",
    "clothes_bottom": "clothes_bottom",
    "skirt_inner": "skirt_inner",
    "skirt_front": "skirt_front",
    "skirt_back": "skirt_back",
    "bottomwear": "bottomwear",
    "leg": "leg_L",
    "thigh": "thigh_L",
    "calf": "calf_L",
    "sock": "sock_L",
    "boot": "boot_L",
    "boot_cuff": "boot_cuff_L",
    "legwear": "legwear",
    "footwear": "footwear",
    "accessories": "accessories",
    "tail": "tail",
    "wings": "wings",
    "objects": "objects",
}


def _standard_order_ranks() -> Dict[str, int]:
    """权威表里每个层名的位置（局部导入以避开 composer ↔ manifest 的导入环）。"""
    from core.segment_engine.composer import LayerComposer

    return {name: index for index, name in enumerate(LayerComposer.STANDARD_LAYER_ORDER)}

_UNKNOWN_SPEC = {
    "name_zh": "未识别部件",
    "group": "accessory",
    "z": 999,
    "sides": False,
}

ALIASES: Dict[str, Tuple[str, Optional[str]]] = {
    "scalp": ("hair_back", None),
    # l2d_sam_iter2 只给一整块头发，没有前后之分；未二次拆分时按后发登记，
    # 与 layers52 的 primary=1 保持一致。
    "hair": ("hair_back", None),
    "hair_front": ("hair_bangs", None),
    "hair_mid": ("hair_bangs", None),
    # split_hair_side_strands 从后发侧束里再拆出的第三束（没有侧别后缀，
    # 名字里带 _inner/_outer），按后发登记，避免清单报「未识别部件」。
    "hair_back_left_inner": ("hair_back", None),
    "hair_back_left_outer": ("hair_back", None),
    "hair_back_right_inner": ("hair_back", None),
    "hair_back_right_outer": ("hair_back", None),
    "face": ("face_base", None),
    "eyes": ("eye_white", None),
    "eyes_left": ("eye_white", "L"),
    "eyes_right": ("eye_white", "R"),
    "iris": ("eye_iris", None),
    "irises": ("eye_iris", None),
    "eyebrows": ("eyebrow", None),
    "arms": ("arm", None),
    "hands": ("hand", None),
    "legs": ("leg", None),
    "leg_upper": ("thigh", None),
    "leg_lower": ("calf", None),
    "thighs": ("thigh", None),
    "calves": ("calf", None),
    "clothes_inner": ("lining", None),
    "clothes_outer": ("outer_body", None),
    "skirt": ("skirt_body", None),
    "tail_ears": ("accessories", None),
    "effects": ("accessories", None),
    "background": ("accessories", None),
    "other": ("accessories", None),
    "skin": ("face_base", None),
    "头发": ("hair_back", None),
    "头发_后": ("hair_back", None),
    "头发_前": ("hair_bangs", None),
    "脸": ("face_base", None),
    "眼睛_白": ("eye_white", None),
    "眼睛_瞳": ("eye_iris", None),
    "眉毛": ("eyebrow", None),
    "嘴巴": ("mouth", None),
    "衣服": ("clothes_top", None),
    "衣服_暗": ("clothes_top", None),
    "topwear": ("clothes_top", None),
    "bottomwear": ("clothes_bottom", None),
    "legwear": ("sock", None),
    "footwear": ("boot", None),
    "irides": ("eye_iris", None),
    "eyewhite": ("eye_white", None),
    "eyelash": ("eyelash", None),
    "ears": ("ear", None),
    "tail": ("tail", None),
    "wings": ("wings", None),
    "objects": ("objects", None),
    "face_detail": ("cheek", None),
    "eye_close": ("eye_white", None),
    "mouth_open": ("mouth", None),
    "mouth_close": ("mouth", None),
    "tooth_top": ("mouth", None),
    "tooth_bottom": ("mouth", None),
    "tongue": ("mouth", None),
    "neckwear": ("accessories", None),
}

REQUIRED_FOR_RIG: List[str] = [
    "hair_back",
    "hair_bangs",
    "face_base",
    "eyebrow:L",
    "eyebrow:R",
    "eye_white:L",
    "eye_white:R",
    "mouth",
    "neck",
    "clothes_top",
    "arm:L",
    "arm:R",
    "hand:L",
    "hand:R",
    "leg:L",
    "leg:R",
]

REQUIRED_FOR_PREVIEW: List[str] = [
    "hair_back",
    "face_base",
    "mouth",
    "neck",
    "clothes_top",
]

_UNSIDED_LIMBS = {
    "arm": "手臂",
    "forearm": "前臂",
    "hand": "手",
    "leg": "腿",
    "sock": "袜",
    "boot": "靴",
}

#: 一条肢体可以由整段表示，也可以由「上段 + 下段」两段表示。
#: 只有两段齐全才算这个槽位齐全；只拆出一段时按缺件处理，绝不把
#: 「大腿单独一层」当成整条腿（那会让绑定器以为小腿也存在）。
#: 每个段给多个候选名，是因为历史输出用过 ``arm_lower`` / ``leg_lower``。
LIMB_CHAINS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "arm": (("arm_upper",), ("forearm", "arm_lower")),
    "leg": (("thigh",), ("calf", "leg_lower")),
}


def _segment_present(alts: Iterable[str], side: str, emitted: Iterable[str]) -> bool:
    emitted_set = emitted if isinstance(emitted, (set, frozenset)) else set(emitted)
    return any(f"{alt}:{side}" in emitted_set for alt in alts)


def _limb_slot_satisfied(slot: str, emitted: Iterable[str]) -> bool:
    """``arm:L`` 这类槽位是否齐全：整段在，或上下两段都在。"""
    emitted_set = emitted if isinstance(emitted, (set, frozenset)) else set(emitted)
    if slot in emitted_set:
        return True
    base, _, side = slot.partition(":")
    chain = LIMB_CHAINS.get(base)
    if not chain or not side:
        return False
    return all(_segment_present(alts, side, emitted_set) for alts in chain)


#: 一个槽位可以由多个子层共同满足（母层在细节拆分后不再存在）。
#: 口部细节拆分把 ``mouth`` 换成上下唇 + 齿/舌，若仍按 ``mouth`` 判缺件，
#: 每张露齿的图都会被误判成「缺嘴」而失去预览资格。
_SLOT_ALTS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "mouth": (("lip_upper",), ("lip_lower",)),
}


def _slot_present(slot: str, emitted: Iterable[str]) -> bool:
    """槽位是否有层满足：母层在，或各子层组都齐全。"""
    emitted_set = emitted if isinstance(emitted, (set, frozenset)) else set(emitted)
    if slot in emitted_set:
        return True
    groups = _SLOT_ALTS.get(slot)
    if not groups:
        return False
    return all(any(alt in emitted_set for alt in group) for group in groups)


def _slot_statuses(
    slot: str,
    status_by_qualified: Dict[str, List[str]],
) -> List[str]:
    """槽位对应的图层状态；整段不在时汇总上下两段的状态。"""
    if slot in status_by_qualified:
        return status_by_qualified[slot]
    base, _, side = slot.partition(":")
    chain = LIMB_CHAINS.get(base)
    if not chain or not side:
        return []
    statuses: List[str] = []
    for alts in chain:
        for alt in alts:
            key = f"{alt}:{side}"
            if key in status_by_qualified:
                statuses.extend(status_by_qualified[key])
                break
    return statuses


def _split_suffix(name: str) -> Tuple[str, Optional[str]]:
    lowered = name.strip().lower()
    for suffix, side in (("_left", "L"), ("_right", "R"), ("_l", "L"), ("_r", "R")):
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)], side
    if lowered.endswith("_左"):
        return lowered[:-2], "L"
    if lowered.endswith("_右"):
        return lowered[:-2], "R"
    return lowered, None


def resolve_part(raw_name: str) -> Tuple[str, str, Optional[str]]:
    """Resolve an emitted layer name to ``(part_id, canonical_base, side)``."""
    base, side = _split_suffix(raw_name or "")

    if base in PART_SPECS:
        spec_sides = bool(PART_SPECS[base].get("sides"))
        return base, base, side if spec_sides else None

    if base in ALIASES:
        canonical, alias_side = ALIASES[base]
        spec_sides = bool(PART_SPECS.get(canonical, {}).get("sides"))
        resolved_side = side or alias_side
        return canonical, canonical, resolved_side if spec_sides else None

    return base or "unknown", "unknown", side


def _qualified(base: str, side: Optional[str]) -> str:
    return f"{base}:{side}" if side else base


@dataclass
class LayerEntry:
    id: str
    name_zh: str
    group: str
    side: Optional[str]
    z: int
    file: str
    #: 内容包围盒 ``[x0, y0, x1, y1]``，闭区间（x1/y1 是最后一个像素）。
    bbox: Optional[List[int]] = None
    pixel_count: int = 0
    confidence: Optional[float] = None
    occluded_by: List[str] = field(default_factory=list)
    amodal_completed: bool = False
    #: 遮挡补全新增的像素数（0 = 没有推断像素）。面积可核对才叫可追溯。
    completed_pixels: int = 0
    #: 补全实际使用的算法（pix2gestalt/diffusion/telea/simple_fill/none）与基线可信度。
    #: 两者必须成对出现，否则视为证据缺失，不得宣称高质量。
    completion_method: Optional[str] = None
    completion_confidence: Optional[float] = None
    #: 拆分降级证据：部件无法按语义安全拆分时的原因原文（只导出候选，不算高质量）。
    split_warnings: List[str] = field(default_factory=list)
    split_limited: bool = False
    derived: bool = False
    depth: Optional[float] = None
    status: str = "detected"
    provenance: str = "unknown"
    source_mask: Optional[str] = None
    parent_group: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name_zh": self.name_zh,
            "group": self.group,
            "side": self.side,
            "z": self.z,
            "file": self.file,
            "bbox": self.bbox,
            "pixel_count": self.pixel_count,
            "confidence": self.confidence,
            "occluded_by": list(self.occluded_by),
            "amodal_completed": bool(self.amodal_completed),
            "completed_pixels": int(self.completed_pixels),
            "completion_method": self.completion_method,
            "completion_confidence": self.completion_confidence,
            "split_warnings": list(self.split_warnings),
            "split_limited": bool(self.split_limited),
            "derived": bool(self.derived),
            "depth": self.depth,
            "status": self.status,
            "provenance": self.provenance,
            "source_mask": self.source_mask,
            "parent_group": self.parent_group or self.group,
        }


@dataclass
class LayerManifest:
    source: str
    canvas: Dict[str, int]
    layers: List[LayerEntry]
    rig_ready: bool
    preview_ready: bool
    missing_required: List[str]
    missing_preview: List[str]
    warnings: List[str]
    quality_ready: bool = False
    manual_required: List[str] = field(default_factory=list)
    #: 源素材里确实不存在、且已记录证据的槽位（不算缺件）。当前只用于眉毛。
    not_applicable: List[str] = field(default_factory=list)
    derived_parts: List[str] = field(default_factory=list)
    #: 逐侧像素覆盖：``{部件基名: {"L": px, "R": px}}``。只作诊断，不是闸门。
    sided_coverage: Dict[str, Dict[str, int]] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    created_at: str = ""
    order_source: str = "static"

    def _ordered_entries(self) -> Tuple[List[LayerEntry], str]:
        if self.layers and all(entry.depth is not None for entry in self.layers):
            return (
                sorted(self.layers, key=lambda e: (float(e.depth), e.z, e.id)),
                "depth",
            )
        return sorted(self.layers, key=lambda e: (e.z, e.id)), self.order_source

    def to_dict(self) -> Dict[str, Any]:
        ordered, order_source = self._ordered_entries()
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "source": self.source,
            "canvas": dict(self.canvas),
            "rig_ready": self.rig_ready,
            "quality_ready": self.quality_ready,
            "preview_ready": self.preview_ready,
            "order_source": order_source,
            "draw_order": [entry.id for entry in ordered],
            "groups": list(GROUP_ORDER),
            "layers": [entry.to_dict() for entry in ordered],
            "missing_required": list(self.missing_required),
            "missing_preview": list(self.missing_preview),
            "manual_required": list(self.manual_required),
            "not_applicable": list(self.not_applicable),
            "derived_parts": list(self.derived_parts),
            "sided_coverage": {key: dict(value)
                               for key, value in self.sided_coverage.items()},
            "warnings": list(self.warnings),
        }


def _sided_coverage(entries: Iterable[LayerEntry]) -> Dict[str, Dict[str, int]]:
    """按部件基名汇总左右侧像素覆盖，供「逐侧诊断」使用。"""
    coverage: Dict[str, Dict[str, int]] = {}
    for entry in entries:
        _part_id, canonical, side = resolve_part(entry.id)
        side = side or entry.side
        if not side:
            continue
        bucket = coverage.setdefault(canonical, {"L": 0, "R": 0})
        bucket[side] += int(entry.pixel_count or 0)
    return coverage


def build_manifest(
    layers: Iterable[Dict[str, Any]],
    source: str,
    canvas: Tuple[int, int],
    warnings: Optional[Iterable[str]] = None,
    eyebrow_evidence: Optional[Dict[str, Any]] = None,
) -> LayerManifest:
    """Build a manifest from layerer output without inventing missing parts."""
    entries: List[LayerEntry] = []
    emitted: set[str] = set()
    extra_warnings: List[str] = list(warnings or [])
    manual_required: List[str] = []
    derived_parts: List[str] = []
    standard_ranks = _standard_order_ranks()

    for layer in layers:
        raw = str(layer.get("name") or "").strip()
        if not raw:
            continue
        part_id, canonical, side = resolve_part(raw)
        spec = PART_SPECS.get(canonical, _UNKNOWN_SPEC)
        if canonical == "unknown":
            extra_warnings.append(f"未识别部件：{raw}")

        # 静态绘制序取自权威表（ADR-003：层序的唯一权威）。此前这里用 PART_SPECS
        # 里另一套 z 权重，两套顺序互相矛盾：权威表把 hair_front 放在脸之后、
        # 权重表把它放在脸之前，于是 PSD 写出的层序与 moc3 的渲染层序对不上。
        # 权威表里没有的部件退回自身权重，并排在权威表部件之后。
        # 优先用原始层名查表（thigh_R / calf_R 这类左右侧名能直接命中），
        # 查不到再用 canonical 部件的代表名。
        standard_rank = standard_ranks.get(raw)
        if standard_rank is None:
            standard_name = STANDARD_ORDER_NAMES.get(canonical)
            if standard_name is not None:
                standard_rank = standard_ranks.get(standard_name)
        z = int(layer.get("z", spec["z"]))
        if standard_rank is not None:
            z = standard_rank
        elif canonical == "unknown":
            z = len(standard_ranks) + spec["z"]

        path = str(layer.get("path") or "")
        confidence = layer.get("confidence")
        depth_value = layer.get("depth")
        depth = float(depth_value) if isinstance(depth_value, (int, float)) else None
        provenance = str(layer.get("provenance") or (
            "derived_geometry" if canonical in DERIVED_PARTS else "unknown"
        ))
        status = str(layer.get("status") or (
            "derived" if provenance in {"derived_geometry", "color_heuristic"}
            else "detected"
        ))
        if status not in {"detected", "derived", "completed", "manual_required", "missing"}:
            extra_warnings.append(f"{raw} 状态无效，按 manual_required 处理")
            status = "manual_required"
        if confidence is None:
            confidence = layer.get("score")
        confidence_value = float(confidence) if isinstance(confidence, (int, float)) else None
        if provenance == "unknown":
            status = "manual_required"
        if status == "detected" and confidence_value is None:
            status = "manual_required"
        if status == "manual_required":
            manual_required.append(raw)
        if status == "derived":
            derived_parts.append(raw)
        entries.append(
            LayerEntry(
                id=raw,
                name_zh=str(layer.get("part_name") or spec["name_zh"]),
                group=str(spec["group"]),
                side=side,
                z=z,
                file=Path(path).name if path else f"{raw}.png",
                bbox=[int(v) for v in layer.get("bbox", [])] or None,
                pixel_count=int(layer.get("pixel_count", 0) or 0),
                confidence=confidence_value,
                occluded_by=list(layer.get("occluded_by", []) or []),
                amodal_completed=bool(layer.get("completed", layer.get("amodal_completed", False))),
                completed_pixels=int(layer.get("completed_pixels", 0) or 0),
                completion_method=(
                    str(layer["completion_method"])
                    if layer.get("completion_method") is not None else None
                ),
                completion_confidence=(
                    float(layer["completion_confidence"])
                    if isinstance(layer.get("completion_confidence"), (int, float))
                    else None
                ),
                split_warnings=[str(w) for w in (layer.get("split_warnings") or [])],
                split_limited=bool(layer.get("split_limited", False)),
                derived=canonical in DERIVED_PARTS,
                depth=depth,
                status=status,
                provenance=provenance,
                source_mask=str(layer.get("source_mask") or raw),
                parent_group=str(layer.get("parent_group") or spec["group"]),
            )
        )
        emitted.add(_qualified(canonical, side))

    if source == SOURCE_COLOR_CLUSTER:
        extra_warnings.append("颜色聚类结果仅用于预览，不可用于 Live2D 绑定")
    elif source == SOURCE_SEMANTIC_FG_GATED:
        extra_warnings.append(
            "前景剪影由学习式模型产出，但部件边界来自颜色启发式，不是部位级学习分割"
        )
    elif source == SOURCE_SEMANTIC_HSV_FALLBACK:
        extra_warnings.append(
            "未找到可用的前景分割模型，已退化为纯颜色启发式，结果仅供预览"
        )
    elif source == SOURCE_CUSTOM:
        extra_warnings.append("部件由用户自定义分割模型产出")

    for base, label in _UNSIDED_LIMBS.items():
        if base in emitted:
            extra_warnings.append(f"{label}未区分左右，无法独立绑定")

    # 上下段只拆出一半：整条肢体仍算缺失，并要求人工确认（不能默默当成齐全）。
    for base, chain in LIMB_CHAINS.items():
        for side in ("L", "R"):
            slot = f"{base}:{side}"
            if slot in emitted:
                continue
            present = [alts[0] for alts in chain
                       if _segment_present(alts, side, emitted)]
            if present:
                extra_warnings.append(
                    f"{slot} 只拆出部分段（{'、'.join(present)}），缺段按未齐全处理")

    missing_preview = [q for q in REQUIRED_FOR_PREVIEW if not _slot_present(q, emitted)]
    missing_required = [q for q in REQUIRED_FOR_RIG
                        if not _limb_slot_satisfied(q, emitted)
                        and not _slot_present(q, emitted)]

    status_by_qualified: Dict[str, List[str]] = {}
    for entry in entries:
        _part_id, canonical, side = resolve_part(entry.id)
        qualified = _qualified(canonical, side or entry.side)
        status_by_qualified.setdefault(qualified, []).append(entry.status)
    for required in REQUIRED_FOR_RIG:
        statuses = _slot_statuses(required, status_by_qualified)
        if not statuses and required in _SLOT_ALTS:
            # 母层已被细节拆分取代：汇总子层状态，避免把齐全的口部判成缺件。
            statuses = [s for group in _SLOT_ALTS[required] for alt in group
                        for s in status_by_qualified.get(alt, [])]
        if not statuses or all(value in {"manual_required", "missing"} for value in statuses):
            manual_required.append(required)
    manual_required.extend(
        entry.id for entry in entries if entry.status == "manual_required"
    )
    manual_required = sorted(set(manual_required))
    missing_required = sorted(set(missing_required) | {
        part for part in manual_required if part in REQUIRED_FOR_RIG
    })

    # 眉毛不适用：源素材本身没有可分离的眉毛笔画时（刘海或设计省略），无论怎么
    # 调检测都取不到像素。按规格 INV-4 / failure_modes 不得生成合成眉毛，只能
    # 如实降级为 N/A —— 不算缺件，但证据与原因必须写进清单（reconsider_if）。
    not_applicable: List[str] = []
    brow_slots = ("eyebrow:L", "eyebrow:R")
    evidence = eyebrow_evidence if isinstance(eyebrow_evidence, dict) else {}
    if evidence.get("absent") and not any(slot in emitted for slot in brow_slots):
        not_applicable.extend(brow_slots)
        missing_required = [s for s in missing_required if s not in brow_slots]
        manual_required = [s for s in manual_required if s not in brow_slots]
        extra_warnings.append(
            "眉毛按不适用处理（不生成合成眉毛）："
            + str(evidence.get("reason") or "源图眉部窗口内没有眉毛像素")
        )

    sided_coverage = _sided_coverage(entries)
    for base, counts in sided_coverage.items():
        left, right = counts["L"], counts["R"]
        if left and right:
            smaller, larger = sorted((left, right))
            if smaller / larger < 0.10:
                extra_warnings.append(
                    f"{base} 左右覆盖差异过大（L={left}px, R={right}px），"
                    "请确认是否误拆或被遮挡")

    exportable_sources = source in _RIG_CAPABLE_SOURCES
    rig_ready = exportable_sources and not missing_required
    # 补全层含推断像素：可以用于导出，但不能宣称「高质量 = 全部来自原画」。
    completed_entries = [entry for entry in entries
                         if entry.status == "completed"]
    if completed_entries:
        inferred = sum(entry.completed_pixels for entry in completed_entries)
        extra_warnings.append(
            f"含遮挡补全层 {len(completed_entries)} 个（共 {inferred} px 为推断像素），"
            "补全区不计入原画证据")
    # INV-A3：补全层必须带上「用什么方法补的、可信度多少」，否则证据链是断的。
    completion_evidence_missing = [
        entry for entry in completed_entries
        if not entry.completion_method or entry.completion_confidence is None
    ]
    if completion_evidence_missing:
        extra_warnings.append(
            "补全层缺少方法/可信度证据："
            + "、".join(entry.id for entry in completion_evidence_missing)
        )
    # INV-A1/A2：拆分降级原因已随层写入；这类层可以导出，但不能算高质量。
    split_limited_entries = [entry for entry in entries if entry.split_limited]
    if split_limited_entries:
        reasons = "；".join(
            f"{entry.id}：{entry.split_warnings[0] if entry.split_warnings else '拆分受限'}"
            for entry in split_limited_entries
        )
        extra_warnings.append(
            f"拆分降级 {len(split_limited_entries)} 项（仍可导出，需人工复核）：{reasons}"
        )
    detection_evidence_ready = all(
        entry.confidence is not None and entry.confidence >= 0.5
        and entry.provenance not in {"unknown", "color_heuristic"}
        for entry in entries if entry.status == "detected"
    )
    quality_ready = (
        rig_ready
        and not manual_required
        and not completed_entries
        and not split_limited_entries
        and not completion_evidence_missing
        and all(
            entry.provenance not in {"unknown", "color_heuristic"}
            and entry.confidence is not None and entry.confidence >= 0.5
            for entry in entries if entry.status == "derived"
        )
        and detection_evidence_ready
    )
    if rig_ready and not quality_ready:
        extra_warnings.append("允许导出候选层，但证据不足或含派生层，未达到高质量 rig-ready")
    preview_ready = not missing_preview
    if not preview_ready:
        extra_warnings.append("缺少必要部件，无法进入预览/分层检查")

    depth_flags = [entry.depth is not None for entry in entries]
    if entries and all(depth_flags):
        order_source = "depth"
    else:
        order_source = "static"
        if any(depth_flags):
            extra_warnings.append("部分图层缺少深度，绘制顺序退回静态层级")

    return LayerManifest(
        source=source,
        canvas={"width": int(canvas[0]), "height": int(canvas[1])},
        layers=entries,
        rig_ready=rig_ready,
        preview_ready=preview_ready,
        missing_required=missing_required,
        missing_preview=missing_preview,
        warnings=extra_warnings,
        quality_ready=quality_ready,
        manual_required=manual_required,
        not_applicable=not_applicable,
        derived_parts=derived_parts,
        sided_coverage=sided_coverage,
        created_at=datetime.now(timezone.utc).isoformat(),
        order_source=order_source,
    )


def write_manifest(output_dir: str, manifest: LayerManifest) -> str:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / MANIFEST_FILENAME
    with path.open("w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle, ensure_ascii=False, indent=2)
    return str(path.resolve())


def is_hollow(missing_preview: Iterable[str]) -> bool:
    """分层是否「空心」：``REQUIRED_FOR_PREVIEW`` 里没有一个部件被认出来。

    为什么线画在这里而不是 ``preview_ready``：实测（本机 14 个真实分层目录 +
    两张受控输入）``preview_ready=false`` 覆盖 10/14 —— 生产默认的 ``semantic``
    路径只缺一个 ``mouth`` 也被判 False，拿它当硬闸门会把用户已经做好的作品
    全部拦死。而 0/5 是另一个性质：不是「部件不完美」，是「根本没认出这是人」
    （纯色图分层 = 0/5；K-means 颜色聚类 = 0/5，清单自己也写着「仅用于预览，
    不可用于 Live2D 绑定」）。空集边界不依赖任何拍脑袋的阈值。
    """
    missing = set(missing_preview)
    return all(part in missing for part in REQUIRED_FOR_PREVIEW)


def validate_manifest(data: Dict[str, Any]) -> List[str]:
    """Return contract violations; empty list means valid."""
    errors: List[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION}")

    canvas = data.get("canvas") or {}
    if not isinstance(canvas.get("width"), int) or canvas.get("width", 0) <= 0:
        errors.append("canvas.width 必须为正整数")
    if not isinstance(canvas.get("height"), int) or canvas.get("height", 0) <= 0:
        errors.append("canvas.height 必须为正整数")

    layers = data.get("layers")
    if not isinstance(layers, list):
        return errors + ["layers 必须为数组"]

    seen: set[str] = set()
    for layer in layers:
        for key in ("id", "name_zh", "group", "z", "file"):
            if key not in layer:
                errors.append(f"图层缺少字段 {key}: {layer!r}")
                break
        layer_id = layer.get("id")
        if layer_id in seen:
            errors.append(f"图层 id 重复: {layer_id}")
        seen.add(layer_id)
        if layer.get("group") not in _GROUP_IDS:
            errors.append(f"未知分组 {layer.get('group')!r}（图层 {layer_id}）")
        if layer.get("side") not in (None, "L", "R"):
            errors.append(f"side 只能是 L/R/null（图层 {layer_id}）")
        if layer.get("status", "detected") not in {
            "detected", "derived", "completed", "manual_required", "missing"
        }:
            errors.append(f"未知图层状态 {layer.get('status')!r}（图层 {layer_id}）")
        if not layer.get("source_mask"):
            errors.append(f"图层缺少 source_mask（图层 {layer_id}）")
        if not layer.get("parent_group"):
            errors.append(f"图层缺少 parent_group（图层 {layer_id}）")
        confidence = layer.get("confidence")
        if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
            errors.append(f"confidence 超出 [0, 1]（图层 {layer_id}）")

    for key in ("rig_ready", "quality_ready", "preview_ready"):
        if not isinstance(data.get(key), bool):
            errors.append(f"{key} 必须为布尔值")

    return errors


def load_manifest(output_dir: str) -> Dict[str, Any]:
    path = Path(output_dir) / MANIFEST_FILENAME
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)
