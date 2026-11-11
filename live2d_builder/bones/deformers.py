#!/usr/bin/env python3
"""Live2D bone hierarchy and warp/rotation deformer generators.

Defines the standard 32-bone Cubism 4 skeleton and the warp/rotation
deformers used for hair swing, body sway, and eye tracking.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np

from core.logger import get_logger
from live2d_builder.layer_naming import (
    ANIMAL_EAR_KEYWORDS,
    HAIR_BACK_KEYWORDS,
    HAIR_KEYWORDS,
    SKIRT_KEYWORDS,
    TAIL_KEYWORDS,
    matches_keywords,
)

log = get_logger("rigging.deformers")


# ======================================================================
# Bone hierarchy
# ======================================================================

class BoneHierarchy:
    """Build a standard Live2D Cubism 4 bone tree adapting to available parts.

    The skeleton follows the canonical 32-bone layout::

        Root
         +-- Body
         |    +-- Torso
         |    |    +-- Chest (breathing pivot)
         |    |    +-- Waist
         |    +-- Neck
         |    |    +-- Head
         |    |         +-- Face
         |    |         +-- Hair_Back
         |    |         +-- Hair_Side_L
         |    |         +-- Hair_Side_R
         |    |         +-- Hair_Front
         |    |         +-- Hair_Top
         |    |         +-- Ear_L
         |    |         +-- Ear_R
         |    |         +-- Eye_L
         |    |         |    +-- Eyeball_L
         |    |         |    +-- Eyelash_L
         |    |         |    +-- Brow_L
         |    |         +-- Eye_R
         |    |         |    +-- Eyeball_R
         |    |         |    +-- Eyelash_R
         |    |         |    +-- Brow_R
         |    |         +-- Nose
         |    |         +-- Mouth
         |    +-- ArmBack_L
         |    |    +-- ForearmBack_L
         |    +-- ArmBack_R
         |    |    +-- ForearmBack_R
         |    +-- Skirt
         |    +-- Leg_L
         |    |    +-- Calf_L
         |    |         +-- Foot_L
         |    +-- Leg_R
         |         +-- Calf_R
         |              +-- Foot_R
    """

    STANDARD_BONES: Dict[str, Dict] = {
        # name -> {parent, group, order}
        "Root":          {"parent": None,            "group": "root",    "order": 0},
        "Body":          {"parent": "Root",          "group": "body",    "order": 1},
        "Torso":         {"parent": "Body",          "group": "body",    "order": 2},
        "Chest":         {"parent": "Torso",         "group": "body",    "order": 3},
        "Waist":         {"parent": "Torso",         "group": "body",    "order": 4},
        "Neck":          {"parent": "Body",          "group": "body",    "order": 5},
        "Head":          {"parent": "Neck",          "group": "head",    "order": 6},
        "Face":          {"parent": "Head",          "group": "face",    "order": 7},
        "Hair_Back":     {"parent": "Head",          "group": "hair",    "order": 8},
        "Hair_Side_L":   {"parent": "Head",          "group": "hair",    "order": 9},
        "Hair_Side_R":   {"parent": "Head",          "group": "hair",    "order": 10},
        "Hair_Front":    {"parent": "Head",          "group": "hair",    "order": 11},
        "Hair_Top":      {"parent": "Head",          "group": "hair",    "order": 12},
        "Ear_L":         {"parent": "Head",          "group": "ears",    "order": 13},
        "Ear_R":         {"parent": "Head",          "group": "ears",    "order": 14},
        "Eye_L":         {"parent": "Head",          "group": "eyes",    "order": 15},
        "Eyeball_L":     {"parent": "Eye_L",         "group": "eyes",    "order": 16},
        "Eyelash_L":     {"parent": "Eye_L",         "group": "eyes",    "order": 17},
        "Brow_L":        {"parent": "Head",          "group": "brows",   "order": 18},
        "Eye_R":         {"parent": "Head",          "group": "eyes",    "order": 19},
        "Eyeball_R":     {"parent": "Eye_R",         "group": "eyes",    "order": 20},
        "Eyelash_R":     {"parent": "Eye_R",         "group": "eyes",    "order": 21},
        "Brow_R":        {"parent": "Head",          "group": "brows",   "order": 22},
        "Nose":          {"parent": "Head",          "group": "nose",    "order": 23},
        "Mouth":         {"parent": "Head",          "group": "mouth",   "order": 24},
        "ArmBack_L":     {"parent": "Body",          "group": "arms",    "order": 25},
        "ForearmBack_L": {"parent": "ArmBack_L",     "group": "arms",    "order": 26},
        "ArmBack_R":     {"parent": "Body",          "group": "arms",    "order": 27},
        "ForearmBack_R": {"parent": "ArmBack_R",     "group": "arms",    "order": 28},
        "Skirt":         {"parent": "Waist",         "group": "clothes", "order": 29},
        # 尾巴挂在腰下：TailSwing 变形器的 parent 必须是真实存在的骨骼，
        # 否则整条尾巴摆链在 moc3 里找不到归属。
        "Tail":          {"parent": "Waist",         "group": "body",    "order": 36},
        "Leg_L":         {"parent": "Waist",         "group": "legs",    "order": 30},
        "Calf_L":        {"parent": "Leg_L",         "group": "legs",    "order": 31},
        "Foot_L":        {"parent": "Calf_L",        "group": "legs",    "order": 32},
        "Leg_R":         {"parent": "Waist",         "group": "legs",    "order": 33},
        "Calf_R":        {"parent": "Leg_R",         "group": "legs",    "order": 34},
        "Foot_R":        {"parent": "Calf_R",        "group": "legs",    "order": 35},
    }

    # Layer-name to bone mapping for automatic assignment
    #
    # 键是**归一化后**的层名（小写、连字符转下划线、去掉数字前缀）。
    # 这份表必须覆盖分层器真正会产出的词表，否则命中不了就落到默认骨骼上。
    # 历史就是栽在这里：表里只有 `face_base`/`eye_left`/`thigh_left` 这类
    # 名字，而实际产出的层叫 `face`/`arm_L`/`sock_L`/`clothes_top`，于是
    # 22 层里有 15 层落进默认的 `Face`——衣服、腿、袜子全部挂在脸的骨骼下，
    # 头一转整件衣服跟着甩（实测 ParamAngleX=±30 时全身一起位移）。
    LAYER_BONE_MAP: Dict[str, str] = {
        # ---- 头发 ----
        "hair_back": "Hair_Back",
        "hair_shadow_back": "Hair_Back",
        "hair_back_left": "Hair_Side_L",
        "hair_back_right": "Hair_Side_R",
        "hair_front": "Hair_Front",
        "hair_bangs": "Hair_Front",
        "hair_mid": "Hair_Front",
        "hair_side": "Hair_Side_L",
        "hair_top": "Hair_Top",
        "ahoge": "Hair_Top",
        "hair_ornament": "Hair_Top",
        "headwear": "Hair_Top",
        "ribbon": "Hair_Top",
        "scalp": "Hair_Back",
        # ---- 脸与五官 ----
        "face": "Face",
        "face_base": "Face",
        "face_blush": "Face",
        "cheek": "Face",
        "face_detail": "Face",
        "eyewear": "Face",
        "ear": "Ear_L",
        "ear_left": "Ear_L",
        "ear_right": "Ear_R",
        "earwear": "Ear_L",
        # 兽耳的常见写法：不映射就会落到默认骨骼（Face），头一晃整只耳朵像贴在脸上。
        "animal_ear": "Ear_L",
        "animal_ear_l": "Ear_L",
        "animal_ear_r": "Ear_R",
        "kemomimi": "Ear_L",
        "kemomimi_l": "Ear_L",
        "kemomimi_r": "Ear_R",
        "cat_ear": "Ear_L",
        "cat_ear_l": "Ear_L",
        "cat_ear_r": "Ear_R",
        "fox_ear": "Ear_L",
        "fox_ear_l": "Ear_L",
        "fox_ear_r": "Ear_R",
        "兽耳": "Ear_L",
        "猫耳": "Ear_L",
        "nose": "Nose",
        "mouth_cavity": "Mouth",
        "mouth_tongue": "Mouth",
        "mouth_teeth": "Mouth",
        "mouth_lowerlip": "Mouth",
        "mouth_upperlip": "Mouth",
        "mouth_open": "Mouth",
        "mouth_close": "Mouth",
        "tooth_top": "Mouth",
        "tooth_bottom": "Mouth",
        "tongue": "Mouth",
        "mouth": "Mouth",
        # ---- 脖子与躯干 ----
        "neck": "Neck",
        "neck_shadow": "Neck",
        "neckwear": "Neck",
        "chest": "Chest",
        "torso": "Torso",
        "body": "Torso",
        "waist": "Waist",
        "waist_hips": "Waist",
        # ---- 服装 ----
        "clothes": "Torso",
        "clothes_inner": "Torso",
        "clothes_outer": "Torso",
        "clothes_top": "Torso",
        "topwear": "Torso",
        "outer_body": "Torso",
        "shirt_fold": "Torso",
        "lining": "Torso",
        "brooch": "Chest",
        "collar": "Neck",
        "shirt_collar": "Neck",
        "outer_collar": "Neck",
        "waistband": "Waist",
        "clothes_bottom": "Skirt",
        "skirt": "Skirt",
        "skirt_body": "Skirt",
        "skirt_front": "Skirt",
        "skirt_back": "Skirt",
        "bottomwear_front": "Skirt",
        "bottomwear_back": "Skirt",
        "bottomwear": "Skirt",
        "skirt_hem": "Skirt",
        "skirt_side": "Skirt",
        # ---- 尾巴 ----
        "tail": "Tail",
        "tail_l": "Tail",
        "tail_r": "Tail",
        "尾巴": "Tail",
        # ---- 配件 ----
        #
        # ``accessories`` 是分层器的**通用兜底桶**，里面可能是头饰、耳饰、项链、
        # 手链……写死成 ``Torso`` 会让头顶的头发碎片跟着躯干转：实测（singer，
        # 该层实为头顶 15..58px 的一撮头发）``ParamBodyAngleZ = 10`` 时它移
        # 78.19px，而同一张图上的头发 / 脸位移 0px —— 头顶出现一条错位的补丁。
        # 交给 ``_infer_from_centroid`` 按位置判定：偏上就是头部，偏中就是胸腰。
        "objects": "Torso",
        "pendant": "Chest",
        "watch": "ForearmBack_L",
        "handwear": "ForearmBack_L",
        # ---- 旧词表（学习式分割/早期管线）----
        "eye_left": "Eye_L",
        "eye_l": "Eye_L",
        "eyeball_left": "Eyeball_L",
        "eyeball_l": "Eyeball_L",
        "eyelash_left": "Eyelash_L",
        "eyelash_l": "Eyelash_L",
        "eye_right": "Eye_R",
        "eye_r": "Eye_R",
        "eyeball_right": "Eyeball_R",
        "eyeball_r": "Eyeball_R",
        "eyelash_right": "Eyelash_R",
        "eyelash_r": "Eyelash_R",
        "brow_left": "Brow_L",
        "brow_l": "Brow_L",
        "brow_right": "Brow_R",
        "brow_r": "Brow_R",
        "thigh_left": "Leg_L",
        "calf_left": "Calf_L",
        "foot_left": "Foot_L",
        "thigh_right": "Leg_R",
        "calf_right": "Calf_R",
        "foot_right": "Foot_R",
        "upper_arm_back_left": "ArmBack_L",
        "forearm_back_left": "ForearmBack_L",
        "hand_back_left": "ForearmBack_L",
        "upper_arm_back_right": "ArmBack_R",
        "forearm_back_right": "ForearmBack_R",
        "hand_back_right": "ForearmBack_R",
        "bg": "Root",
        "background": "Root",
    }

    #: 需要按左右分开绑定的部件 -> ``(左骨, 右骨)``。
    #:
    #: 这些名字在两边都成立（``arm_L`` 与 ``arm_R`` 归一后的主体都是 ``arm``），
    #: 所以不能只靠一张「名字 -> 骨骼」表，必须先把 ``_L/_R`` 后缀拆出来再选边。
    #: 屏幕左侧（x 更小）对应 ``*_L``，与 ``STANDARD_BONES`` 里负 x 的摆位一致。
    SIDED_BONE_MAP: Dict[str, Tuple[str, str]] = {
        "arm": ("ArmBack_L", "ArmBack_R"),
        "upper_arm": ("ArmBack_L", "ArmBack_R"),
        "sleeve": ("ArmBack_L", "ArmBack_R"),
        "forearm": ("ForearmBack_L", "ForearmBack_R"),
        "hand": ("ForearmBack_L", "ForearmBack_R"),
        "cuff": ("ForearmBack_L", "ForearmBack_R"),
        "armwear": ("ForearmBack_L", "ForearmBack_R"),
        "leg": ("Leg_L", "Leg_R"),
        "thigh": ("Leg_L", "Leg_R"),
        "calf": ("Calf_L", "Calf_R"),
        "sock": ("Calf_L", "Calf_R"),
        "legwear": ("Calf_L", "Calf_R"),
        "boot": ("Foot_L", "Foot_R"),
        "foot": ("Foot_L", "Foot_R"),
        "boot_cuff": ("Foot_L", "Foot_R"),
        "footwear": ("Foot_L", "Foot_R"),
        "eyewhite": ("Eyeball_L", "Eyeball_R"),
        "eye_white": ("Eyeball_L", "Eyeball_R"),
        "irides": ("Eyeball_L", "Eyeball_R"),
        "iris": ("Eyeball_L", "Eyeball_R"),
        "eye_iris": ("Eyeball_L", "Eyeball_R"),
        "eyelash": ("Eyelash_L", "Eyelash_R"),
        # 合成上眼睑（见 LayerComposer.synthesize_face_geometry）。挂到睫毛骨而不是
        # 眼球骨：眼皮不该跟着视线平移，只该跟着头走。
        "eyelid": ("Eyelash_L", "Eyelash_R"),
        "eyebrow": ("Brow_L", "Brow_R"),
        "ear": ("Ear_L", "Ear_R"),
    }

    #: 解析不出语义时的兜底骨骼。**不能用 ``Face``/``Head``**：脸的骨骼跟着头
    #: 转，未知层一旦挂上去就会出现"衣服跟着头甩"。躯干是唯一不会误解的选择。
    DEFAULT_BONE = "Torso"

    #: ``_L/_R`` 后缀 -> 侧别。顺序敏感：先长后缀，否则 ``_left`` 会被 ``_l`` 截走。
    _SIDE_SUFFIXES: Tuple[Tuple[str, str], ...] = (
        ("_left", "L"), ("_right", "R"), ("_l", "L"), ("_r", "R"),
        ("_左", "L"), ("_右", "R"),
    )

    def __init__(self) -> None:
        self._tree: Dict[str, Dict] = {}
        self._layer_assignments: Dict[str, str] = {}

    def build(
        self,
        layer_names: List[str],
        centroids: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict:
        """Build the bone tree, keeping only bones that have matching layers.

        The Root, Body, and Head bones are always included. Bones whose
        group has no corresponding layer are pruned (but the structural
        bones Head/Body remain).

        Args:
            layer_names: List of available layer names (lower-case normalised).
            centroids:  Optional dict of layer_name -> (x, y) centroid used
                        to compute bone positions.

        Returns:
            dict with ``tree`` (nested), ``flat`` (list), and ``layer_assignments``.
        """
        normalised = [self._normalise(n) for n in layer_names]
        present_groups: set = set()
        assignments: Dict[str, str] = {}
        span = self._centroid_span(centroids)

        guessed: List[str] = []
        for orig, norm in zip(layer_names, normalised):
            centroid = (centroids or {}).get(orig)
            bone, reason = self.resolve_bone(norm, centroid, span)
            if reason != "map":
                guessed.append(f"{norm}->{bone}({reason})")
            assignments[orig] = bone
            info = self.STANDARD_BONES.get(bone)
            if info:
                present_groups.add(info["group"])
        if guessed:
            log.info(f"语义未直接命中的图层按位置/兜底绑定: {sorted(guessed)}")

        # Always keep root/body/head structural bones
        for structural in ("Root", "Body", "Neck", "Head", "Torso"):
            self.STANDARD_BONES.get(structural)  # ensure exists

        # Build flat list
        flat: List[Dict] = []
        for name, info in self.STANDARD_BONES.items():
            if name not in ("Root", "Body", "Neck", "Head", "Torso"):
                if info["group"] not in present_groups:
                    continue
            flat.append({
                "name": name,
                "parent": info["parent"],
                "group": info["group"],
                "order": info["order"],
                "children": [],
            })

        # Wire children
        by_name = {b["name"]: b for b in flat}
        for b in flat:
            if b["parent"] and b["parent"] in by_name:
                by_name[b["parent"]]["children"].append(b["name"])

        # Compute positions
        positions = self.get_bone_positions(centroids or {})
        for b in flat:
            pos = positions.get(b["name"], (0.0, 0.0))
            b["x"] = round(pos[0], 2)
            b["y"] = round(pos[1], 2)

        self._tree = by_name.get("Root", {"name": "Root", "children": []})
        self._layer_assignments = assignments

        log.info(f"Built bone hierarchy with {len(flat)} bones")
        return {
            "tree": self._tree,
            "flat": flat,
            "layer_assignments": assignments,
        }

    def to_json(self) -> List[Dict]:
        """Export bones in a Cubism Editor import-friendly format.

        Returns:
            List of bone dicts with keys: name, parent, x, y, length,
            index, parent_index.
        """
        result: List[Dict] = []
        flat_bones: List[Dict] = []

        def _collect(node: Dict) -> None:
            flat_bones.append(node)
            for child_name in node.get("children", []):
                # children are names, look up from _tree
                pass

        # Rebuild from STANDARD_BONES ordering
        for name, info in self.STANDARD_BONES.items():
            flat_bones.append({"name": name, "parent": info["parent"]})

        name_to_idx = {b["name"]: i for i, b in enumerate(flat_bones)}
        for i, b in enumerate(flat_bones):
            parent_idx = name_to_idx.get(b["parent"], -1) if b["parent"] else -1
            result.append({
                "name": b["name"],
                "parent": b["parent"] or "",
                "index": i,
                "parent_index": parent_idx,
                "x": 0.0,
                "y": 0.0,
                "length": 0.0,
            })
        return result

    @staticmethod
    def _split_side(name: str) -> Tuple[str, Optional[str]]:
        """拆出 ``_L/_R`` 后缀：``arm_l`` -> ``("arm", "L")``。"""
        for suffix, side in BoneHierarchy._SIDE_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                return name[: -len(suffix)], side
        return name, None

    @staticmethod
    def _centroid_span(
        centroids: Optional[Dict[str, Tuple[float, float]]],
    ) -> Optional[Tuple[float, float, float, float]]:
        """所有图层质心的外包框 ``(x0, y0, x1, y1)``，用作身体比例尺。

        没有画布尺寸也能自洽：质心的上下极值近似"头顶到脚"，横向极值近似
        "身体宽度"。位置推断只用相对比例，所以不要求它精确。
        """
        if not centroids:
            return None
        xs = [float(c[0]) for c in centroids.values()]
        ys = [float(c[1]) for c in centroids.values()]
        return min(xs), min(ys), max(xs), max(ys)

    def resolve_bone(
        self,
        norm: str,
        centroid: Optional[Tuple[float, float]] = None,
        span: Optional[Tuple[float, float, float, float]] = None,
    ) -> Tuple[str, str]:
        """归一化层名 -> ``(骨骼名, 判定依据)``。

        判定依据用于日志与回归测试：``map`` 是词表直接命中，``sided`` 是按
        ``_L/_R`` 选边，``position`` 是按质心落在身体哪一段推断，``default``
        是兜底。以前的实现在第 3 步就掉进 ``Face`` 了，衣服因此跟着头动。

        入参可以先不归一化（``arm_L`` 与 ``arm_l`` 等价），内部会归一。
        """
        norm = self._normalise(norm)
        bone = self.LAYER_BONE_MAP.get(norm)
        if bone:
            return bone, "map"

        base, side = self._split_side(norm)
        if base != norm:
            bone = self.LAYER_BONE_MAP.get(base)
            if bone:
                return bone, "map"

        if base in self.SIDED_BONE_MAP:
            left, right = self.SIDED_BONE_MAP[base]
            if side is None and centroid is not None and span is not None:
                side = "L" if float(centroid[0]) <= (span[0] + span[2]) / 2.0 else "R"
            return (left if side != "R" else right), "sided"

        if base in self.LAYER_BONE_MAP:
            return self.LAYER_BONE_MAP[base], "map"

        # 词表只能穷举有限写法；中文与其它后端命名走关键词兜底，
        # 否则「裙子」「尾巴」「cat_ear_L」都会掉到位置推断/默认骨骼。
        keyword_bone = self._bone_from_keywords(norm, base, side)
        if keyword_bone:
            return keyword_bone, "keyword"

        inferred = self._infer_from_centroid(centroid, span)
        if inferred:
            return inferred, "position"
        return self.DEFAULT_BONE, "default"

    @staticmethod
    def _bone_from_keywords(norm: str, base: str, side: Optional[str]) -> str:
        """按 layer_naming 的关键词表兜底骨骼；没命中返回空串。"""
        probes = {norm, base}

        def hit(keywords) -> bool:
            return any(matches_keywords(probe, keywords) for probe in probes)

        if hit(ANIMAL_EAR_KEYWORDS):
            return "Ear_R" if side == "R" else "Ear_L"
        if hit(TAIL_KEYWORDS):
            return "Tail"
        if hit(SKIRT_KEYWORDS):
            return "Skirt"
        if hit(HAIR_BACK_KEYWORDS):
            return "Hair_Back"
        if hit(HAIR_KEYWORDS):
            return "Hair_Front"
        return ""

    def _infer_from_centroid(
        self,
        centroid: Optional[Tuple[float, float]],
        span: Optional[Tuple[float, float, float, float]],
    ) -> Optional[str]:
        """按质心落在身体的哪一段猜骨骼。

        比例取七头身立绘的经验值：头 0~20%、肩胸 20~34%、躯干 34~50%、
        腰胯 50~68%、腿 68% 以下。猜错也只是"衣服跟躯干走"这种可接受的
        近似，远好过以前一律挂到 ``Face`` 上。

        最上面一段返回 **``Hair_Top`` 而不是 ``Head``**：``Head`` 是头部转动的
        枢轴，``get_bone_positions`` 会拿归到它名下的图层质心当关节点。头顶的
        图层（如 ``accessories``）一旦归到 ``Head``，枢轴就从颈根窜到头顶 ——
        实测 ``ParamAngleZ = 30`` 时脸被甩出 61.90px，而正确构建里同一参数下
        脸只移 15.56px、整颗头各层共用同一个旋转矩阵。
        ``Hair_Top`` 与 ``Head`` 同属头部总成（键形、权重完全一致），但不参与
        枢轴测量。
        """
        if centroid is None or span is None:
            return None
        x0, y0, x1, y1 = span
        height = max(1.0, y1 - y0)
        frac = (float(centroid[1]) - y0) / height
        if frac < 0.20:
            return "Hair_Top"
        if frac < 0.34:
            return "Chest"
        if frac < 0.50:
            return "Torso"
        if frac < 0.68:
            return "Waist"
        side = "L" if float(centroid[0]) <= (x0 + x1) / 2.0 else "R"
        return f"Leg_{side}"

    def get_bone_positions(
        self,
        centroids: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """骨骼枢轴，**图像坐标**（左上原点、y 向下）。

        为什么必须是图像坐标：调用方 ``moc3_pipeline`` 会拿这些点做
        ``to_model_xy``（减去画布中心）。曾经这里返回的是"以画布中心为原点"
        的比例值（``Head = (0, -40)``），再被减一次半画布，头骨枢轴就跑到
        画布左下角外，头一转整颗头绕着角落甩出去。

        没有质心时退回比例模型（同样按"画布中心为原点"）；一旦有质心，就
        全部换到图像坐标，并由子骨骼补出 ``Head/Root/Body/Chest`` 这些没有
        自己图层的结构骨骼。
        """
        if not centroids:
            return dict(self._proportion_positions())

        positions = dict(self._proportion_positions())
        span = self._centroid_span(centroids)
        measured: set = set()
        for layer_name, (cx, cy) in centroids.items():
            norm = self._normalise(layer_name)
            bone, _reason = self.resolve_bone(norm, (cx, cy), span)
            if bone in positions:
                positions[bone] = (float(cx), float(cy))
                measured.add(bone)

        # 结构骨骼没有自己的图层：从已测量到的子骨骼推，保证枢轴落在角色身上
        if "Head" not in measured:
            if "Face" in measured:
                head_y = positions["Neck"][1] if "Neck" in measured else positions["Face"][1]
                positions["Head"] = (positions["Face"][0], head_y)
            elif "Neck" in measured:
                positions["Head"] = positions["Neck"]
        for bone, source in (("Body", "Waist"), ("Chest", "Torso"), ("Root", "Waist")):
            if bone not in measured and source in measured:
                positions[bone] = positions[source]

        return positions

    @staticmethod
    def _proportion_positions() -> Dict[str, Tuple[float, float]]:
        """比例模型兜底（**画布中心为原点**）。

        只在拿不到任何图层质心时使用（例如直接构造骨骼树做单元测试）。
        真实管线一律走 :meth:`get_bone_positions` 的图像坐标分支。
        """
        return {
            "Root":          (0.0,    0.0),
            "Body":          (0.0,   50.0),
            "Torso":         (0.0,  120.0),
            "Chest":         (0.0,  100.0),
            "Waist":         (0.0,  200.0),
            "Neck":          (0.0,   60.0),
            "Head":          (0.0,  -40.0),
            "Face":          (0.0,  -30.0),
            "Hair_Back":     (0.0,  -80.0),
            "Hair_Side_L":   (-60.0, -40.0),
            "Hair_Side_R":   ( 60.0, -40.0),
            "Hair_Front":    (0.0,  -90.0),
            "Hair_Top":      (0.0, -120.0),
            "Ear_L":         (-70.0, -20.0),
            "Ear_R":         ( 70.0, -20.0),
            "Eye_L":         (-35.0, -40.0),
            "Eyeball_L":     (-35.0, -40.0),
            "Eyelash_L":     (-35.0, -30.0),
            "Brow_L":        (-35.0, -60.0),
            "Eye_R":         ( 35.0, -40.0),
            "Eyeball_R":     ( 35.0, -40.0),
            "Eyelash_R":     ( 35.0, -30.0),
            "Brow_R":        ( 35.0, -60.0),
            "Nose":          (0.0,  -10.0),
            "Mouth":         (0.0,   10.0),
            "ArmBack_L":     (-90.0,  80.0),
            "ForearmBack_L": (-130.0, 150.0),
            "ArmBack_R":     ( 90.0,  80.0),
            "ForearmBack_R": ( 130.0, 150.0),
            "Skirt":         (0.0,  220.0),
            "Tail":          (0.0,  200.0),
            "Leg_L":         (-30.0, 240.0),
            "Calf_L":        (-30.0, 340.0),
            "Foot_L":        (-30.0, 440.0),
            "Leg_R":         ( 30.0, 240.0),
            "Calf_R":        ( 30.0, 340.0),
            "Foot_R":        ( 30.0, 440.0),
        }

    @staticmethod
    def _normalise(name: str) -> str:
        return normalise_layer_name(name)


def normalise_layer_name(name: str) -> str:
    """Normalise a layer name for lookup.

    lower-case + separators to underscores, and strip a leading numeric
    index prefix. Layer PNGs are exported as ``NNN_part.png``
    (``000_arms``, ``005_face``), so without the prefix strip every
    lookup falls through to the default bone and all layers end up
    parented to ``Face`` — which silently disables parameter keyforms.
    """
    norm = name.strip()
    # 驼峰先拆开再小写：PSD 与部分后端导出 ``HairFront`` / ``hairBack``，
    # 不拆就变成 ``hairfront``，骨骼表与变形器 targets 一个都命中不了。
    norm = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", norm)
    norm = norm.lower().replace(" ", "_").replace("-", "_")
    norm = re.sub(r"^\d+_", "", norm)
    return norm


# ======================================================================
# Deformer hierarchy (warp / rotation deformers)
# ======================================================================

class DeformerHierarchy:
    """Create warp and rotation deformers for hair, body, and eye tracking.

    Warp deformers use a 2D control grid; rotation deformers pivot around
    a single point. These map to Live2D Cubism's ArtMesh deformers.
    """

    # ---- 两个 parent 键，别混 ----
    #
    # ``"parent"``：这条变形器**挂在哪个骨骼**下（Part/Bone 归属），由
    #   model3.json / 骨骼层级那一层消费，导出层不读它当变形器父级。
    # ``"under"``：这条变形器**挂在哪条变形器**下（值 = 本表里的 ``name``），
    #   由 ``moc3_pipeline.resolve_deformer_parents`` 解析成 moc3 的
    #   ``parent_deformer_id``，于是子级继承父级的转动、同一个参数不必再各
    #   拟合一份键形带。空 = 根级。Task 3 起这张表**不再是扁平林**：
    #   BodySway(根) → HeadRig → 表情/头发变形器，最深三级（官方 Core 实测过的
    #   包络就是三级，见 ``tests/unit/test_moc3_deformers.py``
    #   ``test_authored_head_field_chain_stops_at_the_measured_depth_three``）。
    #
    # 之所以另起一个键：两个 ``parent`` 语义在不同层，重载会把「骨骼归属」和
    # 「变形链」搅成一团，而且 ``parent`` 早就被每条写着，改判它等于悄悄重排
    # 全部产物。
    #
    # 写 ``under`` 时不必操心表的顺序：写出前 ``moc3_model`` 会按父链做拓扑
    # 规范化（``parent_first_deformer_order``），父级在后也一样（规范化会搬动
    # 槽位序，产物按被官方 Core 量过的 parent-first 那一种写出）。但**层数**
    # 要操心：超过 ``TRUNK_MAX_CHAIN_DEPTH`` 的
    # 那一级会被 ``build_deformers`` 退回无父级并留痕，未验证的深度不许静默进产物。
    DEFAULT_WARP_DEFORMERS: List[Dict] = [
        # ---- 头部总成：整个头只转一次 ----
        #
        # 头的转动**只**存在于 HeadRig 这一条带里；挂在其下的网格靠 moc3 的
        # parent_deformer_indices 继承（Task 1 已用官方 Core 验证逐级求值）。
        # 之前每个部件各拟合一份 ParamAngle*，六个独立场之间的配对距离没人约束
        # —— 实测头内部 27px 漂移就来自这里（36c3fcc4 重建的歌手件，满幅
        # ParamAngleZ=30 / ParamBodyAngleZ=10：eyelid_left↔eyewhite_left 27.1272px、
        # eyewhite_left↔nose 27.1268px、cheek↔mouth 27.1267px、
        # face↔eyewhite_left 27.1259px。逐网格拟合给出的不是角度差（两侧都是
        # 40.000°）而是**圆心**差 39.7px —— θ(rad)·d ≈ 27.7px，与实测同源。
        #
        # ``targets`` 里的层名必须**逐个显式列出**：``build()`` 会拿真实层名过滤
        # targets（``build`` 内的 ``_matched_targets``，deformers.py:783 @ 36c3fcc4），
        # 没列上的层既不会报错也不会留痕，只是**不随头转**。三类必须点名的层：
        #   * ``cheek`` / ``tear_left`` / ``tear_right`` —— 它们原本的 CheekFollow /
        #     TearFollow 被删掉了，而层名只活在 ``OPACITY_KEYFORM_LAYERS`` 里
        #     （moc3_pipeline.py:1084-1088 @ 36c3fcc4）；漏掉就是「腮红留在原地滑出脸外」。
        #   * ``eyelid_left`` / ``eyelid_right`` —— 合成上眼睑，此前谁都不挂、
        #     只靠自己的键形带随头转；撤烘焙后不接住就是眼皮原地不动。
        #   * ``ear_left`` / ``ear_right`` / ``accessories`` —— 耳朵与头顶饰件在
        #     真实层名里可能出现，且都在头的外沿。
        # 淡入层只交出**几何归属**：ParamCheek / ParamTears 的不透明度键形照旧留在
        # 网格上（一个网格只能挂一条带，那是局部表情，本来就该相对父级动）。
        # 头是挂在身子上的：``under: "BodySway"`` 让整颗头跟着躯干倾摆走。
        # 中间插一条 ``HeadRotationZ``（rotation，绕 Head 关节转 ParamAngleZ）：
        # BodySway(1) → HeadRotationZ(2) → HeadRig(3) → 表情/头发(4)，
        # 正好停在官方 Core 量过的链深包络上（TRUNK_MAX_CHAIN_DEPTH = 4）。
        #
        # Z 侧倾从这条 warp 的 angle_z 挪到 HeadRotationZ 上：刚体旋转是 rotation
        # 变形器的本职，warp 只是用逐点位移模拟它。两条边的坐标口径差异（rotation
        # 子树被父 warp 平移、origin 用父 warp 的 (s,t)）已在 moc3_model 写出侧落地，
        # 并用官方 Core 逐字节验收（BodySway→HeadRotationZ→HeadRig 链在 Z=±30°
        # 与「顶点预旋同角度」完全一致）。
        #
        # 所以这条 warp 只剩 X/Y：profile 用 ``head_xy``（两轴），不能再写三轴 ——
        # ``_profile_grids`` 按参数个数精确匹配角色序列，多一个少一个都会静默退回静态。
        {"name": "HeadRig",  "parent": "Head", "under": "HeadRotationZ",
         "rows": 4, "cols": 3, "type": "warp", "profile": "head_xy",
         "parameters": ["ParamAngleX", "ParamAngleY"],
         # ear_L / ear_R 是 19 类部位后端（l2d_sam_iter2）的层名写法；
         # 不点名的话人类耳朵不随头转（ear_left/right 是旧管线写法）。
         "targets": ["face", "nose", "accessories", "ear_left", "ear_right",
                    "ear_L", "ear_R",
                    "cheek", "tear_left", "tear_right",
                    "eyelid_left", "eyelid_right"]},
        # ---- 头发摆动：由 ParamHairSwing 驱动，发根钉住、发梢摆 ----
        {"name": "HairFrontSwing", "parent": "Head", "under": "HeadRig",
         "rows": 3, "cols": 3,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamHairSwing"],
         # 19 类部位解析后端（l2d_sam_iter2）只给一整块 ``hair``，没有前后之分。
         # 不点名它就一个变形器都挂不上、ParamHairSwing 照算但没人消费 ——
         # 预览里头发纹丝不动。整块只有一条摆链，挂前发这条（快摆）。
         "targets": ["hair_front", "hair_bangs", "hair_mid", "hair_side", "hair"],
         # 通用命名：HairFront / 前发 / hair1 都要能命中；后发从头发大类里剔除，
         # 否则同一块后发会被前发与后发两条摆链同时声明。
         "target_keywords": HAIR_KEYWORDS,
         "exclude_keywords": HAIR_BACK_KEYWORDS},
        # 后发是独立的慢摆链，读自己的 ParamHairSwingBack：
        # 与 HairFront 共用 ParamHairSwing 时内核只保留一条输出（实测旧产物里
        # 两个组的输出完全一样），后发的三级钟摆等于白算。
        {"name": "HairBackSwing",  "parent": "Head", "under": "HeadRig",
         "rows": 4, "cols": 2,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamHairSwingBack"],
         "targets": ["hair_back", "hair_shadow_back"],
         "target_keywords": HAIR_BACK_KEYWORDS,
         # 左右侧后发各有自己的摆链（HairSideSwing_L/R），这里不重复声明。
         "exclude_keywords": ("hair_back_left", "hair_back_right", "左后发", "右后发")},
        {"name": "HairSideSwing_L", "parent": "Head", "under": "HeadRig",
         "rows": 2, "cols": 2,
         "type": "warp", "profile": "hair_sway",
         # 侧束有自己的物理参数：与中束共用 ParamHairSwingBack 时内核只保留一条
         # 输出，侧束与中束的相位差被抹平，四束拆分就白拆了。
         "parameters": ["ParamHairSwingSideL"],
         "targets": ["hair_back_left"]},
        {"name": "HairSideSwing_R", "parent": "Head", "under": "HeadRig",
         "rows": 2, "cols": 2,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamHairSwingSideR"],
         "targets": ["hair_back_right"]},
        # ---- 兽耳摆动：由 ParamEarSwing 驱动（AnimalEars 物理组的输出）----
        #
        # 只点名**兽耳专属**层名：人类的 ear_left / ear_right 归 HeadRig，
        # 一块网格只能挂一个变形器，两边都写会互相覆盖。
        {"name": "EarSwing", "parent": "Head", "under": "HeadRig",
         "rows": 2, "cols": 2,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamEarSwing"],
         "targets": ["animal_ear", "animal_ear_L", "animal_ear_R",
                     "kemomimi", "kemomimi_L", "kemomimi_R"],
         # 通用命名：cat_ear / fox_ear / beast_ear / 猫耳；候选表里刻意不含
         # 单独的 ``ear``，否则 19 类的 earwear（耳饰）会被当成会摆的兽耳。
         "target_keywords": ANIMAL_EAR_KEYWORDS},
        # ---- 躯干：侧倾 / 前后倾 / 摆动 / 呼吸（胯部不动，越靠肩位移越大）----
        #
        # 脖子必须跟躯干挂在同一条变形器上：领口（``collar``）画在脖子上，只有领子
        # 会倾摆而脖子不动，衣服就像从脖子上滑走。实测（singer，
        # ``ParamBodyAngleY = 10``）领子横移 26.03px、上身衣服 14.67px，脖子 **0px**。
        # 脖子的 ``ParamBodyAngleZ`` 键形带不受影响 —— 键形带与变形器是两层。
        {"name": "BodySway",       "parent": "Torso", "rows": 3, "cols": 2,
         "type": "warp", "profile": "torso",
         "parameters": ["ParamBodyAngleX", "ParamBodyAngleY",
                        "ParamBodySway", "ParamBreath"],
         "targets": ["clothes_top", "clothes_inner", "clothes_outer",
                     "waistband", "chest", "body"]},
        # 导出阶段把 clothes_top 沿 neck 下缘 + 12px 重叠裁成一个窄片，登记为
        # clothes_top_neck 并挂进这条带；原 clothes_top 的下片仍留在 BodySway。
        # 单独挂整件上衣曾让肩线跟头、与手臂撕开 112.4px；横向渐隐方案因为
        # 键形先于变形器、权重坐标被 body-Z 移出场，在 Core 上洞数增到 1219。
        # 几何重叠片是最小可验变体：交线沿 NeckBand 现有 neck 网格下缘，带内空间
        # 不再需要外推到肩线；12px 重叠给变形近似留容差。领口 collar 同样由 NeckBand
        # 接住，否则 neck/过渡片随头转、领口仍随躯干，复合姿态会留下黑色楔缝。
        #
        # 控制网格沿颈宽密布，覆盖网格只含 neck 与窄过渡片；切分段不会把躯干下缘
        # 拉进变形框。每行/列数量依实测输入尺寸微调，静止几何仍由网格本身保持。
        {"name": "NeckBand", "parent": "Neck", "under": "BodySway",
         "rows": 10, "cols": 21, "type": "warp", "profile": "neck_band",
         "axis_layer": "neck",
         "parameters": ["ParamAngleX", "ParamAngleY", "ParamAngleZ"],
         "targets": ["neck", "collar"]},
        # 裙摆：腰线钉住、下摆按 ParamSkirtSwing 横摆；under=BodySway 让整条裙子
        # 先跟着躯干倾摆，再叠加自己的惯性摆动。
        #
        # targets 必须同时点名 19 类后端的 ``bottomwear`` 与旧管线的
        # ``clothes_bottom`` / ``skirt_body``：少一个就是「physics3 里有 Skirt 组、
        # 裙子却不动」。此前这条变形器既没有 parameters 也没有 profile，
        # 只会被当成静止网格导出。
        {"name": "SkirtSway",      "parent": "Skirt", "under": "BodySway",
         "rows": 4, "cols": 3,
         "type": "warp", "profile": "skirt_sway",
         "parameters": ["ParamSkirtSwing"],
         "targets": ["skirt", "bottomwear", "clothes_bottom", "skirt_body"],
         # 通用命名：SkirtFront / 裙摆 / 短裙 都能命中。
         "target_keywords": SKIRT_KEYWORDS},
        # ---- 尾巴摆动：由 ParamTailSwing 驱动（Tail 物理组的输出）----
        #
        # ``tail`` 按整词匹配，``detail`` 不会误命中；中文层名（尾巴）走子串。
        {"name": "TailSwing", "parent": "Tail", "under": "BodySway",
         "rows": 3, "cols": 2,
         "type": "warp", "profile": "tail_sway",
         "parameters": ["ParamTailSwing"],
         "targets": [],
         "target_keywords": TAIL_KEYWORDS},
        # ---- 表情形变：参数写死在说明里（这些效果没有对应骨骼，无从推导）----
        #
        # 放在变形器层而不是网格键形层，是因为网格只能挂**一个**参数带：眼睛网格
        # 已经用双眼球键形（ParamEyeBallX/Y）、嘴与眉已经用头部三轴键形
        # （ParamAngleX/Y/Z）；变形器与网格键形是两层，可以同时生效。
        # 一个变形器也只能挂一个带，所以同一部件的多个参数合成一条**多轴带**
        # （见 moc3_pipeline.DEFORMER_PROFILES / moc3_model.WarpDeformerSpec.axes）。
        {"name": "MouthOpen", "parent": "Mouth", "under": "HeadRig",
         "rows": 2, "cols": 3,
         "type": "warp", "profile": "mouth",
         "parameters": ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
         "targets": ["mouth", "mouth_open", "mouth_close"]},
        # 口内：与嘴线同源、但**张得更深**，所以在嘴唇下方露出一条腔体。
        # 若挂在 MouthOpen 下（同样的 2.5×），它会永远正好被嘴唇盖住——等于没做。
        {"name": "MouthCavity", "parent": "Mouth", "under": "HeadRig",
         "rows": 2, "cols": 2,
         "type": "warp", "profile": "mouth_cavity",
         "parameters": ["ParamMouthOpenY"],
         "targets": ["mouth_cavity", "mouth_inner"]},
        # 闭眼：眼球整体**向上收拢**进上睫毛线里（``eye`` profile 的轴心取眼睛
        # 轮廓上沿）。眼睛下面已经画好了「闭眼图」（脸层肤色 + 原画上睫毛，见
        # LayerComposer._synthesize_eyelid），收拢后露出来就是闭眼的样子。
        # 闭眼图是**静态**的，所以不占变形器：它由 EyeBlink 的收拢动作"让出来"。
        #
        # Task 3 以前这里还带头部三轴（``eye_head``）：眼球网格的键形带被双眼球跟踪
        # （ParamEyeBallX/Y）占满，头部转动只能压在变形器层；而一块网格只能挂一个
        # 变形器，所以眨眼与转头合成一条多轴带。现在那条带里**只剩眨眼**，头的三轴
        # 由 ``under: "HeadRig"`` 继承 —— 两条各拟合一份的结果是同一刚体角下圆心差
        # 39.7px（实测满幅 eyelid_left↔eyewhite_left 27.1272px），眼球相对眼睑滑开。
        # 局部表情（``eye_track_keyforms`` 那份）本来就该相对父级动，**保留**。
        #
        # 控制网格用 4×4 点（3 格）：1 格时透视项在 84px 宽的眼区上被双线性近似
        # 掉 0.35px，3 格降到 0.07~0.15px，再加密（5 格）无改善。同步把
        # moc3 从 453248 涨到 460160 字节。
        {"name": "EyeBlink_L", "parent": "Eye_L", "under": "HeadRig",
         "rows": 4, "cols": 4,
         "type": "warp", "profile": "eye",
         "parameters": ["ParamEyeLOpen"],
         "targets": ["eyewhite_left", "irides_left", "eyelash_left",
                     "eyes_left", "eye_left"]},
        {"name": "EyeBlink_R", "parent": "Eye_R", "under": "HeadRig",
         "rows": 4, "cols": 4,
         "type": "warp", "profile": "eye",
         "parameters": ["ParamEyeROpen"],
         "targets": ["eyewhite_right", "irides_right", "eyelash_right",
                     "eyes_right", "eye_right"]},
        {"name": "BrowMove_L", "parent": "Brow_L", "under": "HeadRig",
         "rows": 2, "cols": 3,
         "type": "warp", "profile": "brow",
         "parameters": ["ParamBrowLY", "ParamBrowLAngle", "ParamBrowLForm"],
         "targets": ["eyebrow_L", "brow_L", "brow_left"]},
        {"name": "BrowMove_R", "parent": "Brow_R", "under": "HeadRig",
         "rows": 2, "cols": 3,
         "type": "warp", "profile": "brow", "mirror": True,
         "parameters": ["ParamBrowRY", "ParamBrowRAngle", "ParamBrowRForm"],
         "targets": ["eyebrow_R", "brow_R", "brow_right"]},
        # ---- 表情淡入层：Task 3 起不再有各自的头部场 ----
        #
        # 这里原本是 CheekFollow / TearFollow 两条 4×4 warp：腮红 / 眼泪的网格键形带
        # 被不透明度（ParamCheek / ParamTears）占满，头部三轴进不去，而它们又没有别的
        # 变形器 —— 实测 ParamAngleZ=30 时脸转 13.15px、cheek / tear 位移 0px，腮红和
        # 泪痕留在原地滑出脸外，所以当时补了「只含头部三轴的 warp」这条路。
        #
        # 那是**六个独立拟合的头部场**中的两个，正是头内部 27.1272px 开口的来源，
        # 于是本任务把它们删掉、把 ``cheek`` / ``tear_left`` / ``tear_right`` 三个层名
        # 显式交给上面的 HeadRig（不列名就会被 ``_matched_targets`` 静默丢掉，重演
        # 「腮红留在原地」）。不透明度键形照旧留在网格上 —— 淡入层交出的只是几何归属。
        # ``DEFORMER_PROFILES["head"]`` 与 ``_head_options`` 这条**能力**保留（HeadRig
        # 用的就是它），只是不再各部件一份。
        # ---- 肩部抬落（叠加躯干倾摆）----
        #
        # 没有独立的肩部图层，只能抬整条上臂（Live2D 里肩参数本来也会带上臂）。
        # 幅度刻意小（上臂高度的 12%），避免看起来像"举手"。
        #
        # 袖口 / 手必须跟手臂挂在**同一条**变形器上：它们是小臂末端的独立图层，
        # 不跟着抬就会在腕口裂开。实测（singer，ParamShoulderL=1）手臂抬 38.40px、
        # cuff_L / hand_L 位移 0px；ParamShoulderR=1 时手臂 56.88px、cuff_R / hand_R
        # 0px。同一条变形器让三者共用同一个网格范围，位移逐点相同。
        #
        # 躯干的四个倾摆轴也要挂进来：手臂画在身子两侧，只有衣服倾摆而手臂不动，
        # 肩窝处就会错开（实测 ``ParamBodyAngleY = 10`` 时上身衣服横移 14.67px、
        # 手臂 0px）。抬落仍按这条变形器自己的成员高度算，幅度不变。
        {"name": "ShoulderLift_L", "parent": "ArmBack_L", "rows": 2, "cols": 2,
         "type": "warp", "profile": "lift_lean",
         "parameters": ["ParamShoulderL", "ParamBodyAngleX", "ParamBodyAngleY",
                        "ParamBodySway", "ParamBreath"],
         "targets": ["arm_L", "upper_arm_back_left", "sleeve_L",
                     "cuff_L", "hand_L"]},
        {"name": "ShoulderLift_R", "parent": "ArmBack_R", "rows": 2, "cols": 2,
         "type": "warp", "profile": "lift_lean",
         "parameters": ["ParamShoulderR", "ParamBodyAngleX", "ParamBodyAngleY",
                        "ParamBodySway", "ParamBreath"],
         "targets": ["arm_R", "upper_arm_back_right", "sleeve_R",
                     "cuff_R", "hand_R"]},
    ]

    # ---- rotation 变形器 ----
    #
    # 眼睛两条仍是「不落地」的定义（保留只为标明意图），HeadRotationZ 作为
    # **可选能力**保留在 ``HEAD_ROTATION`` 里但**不进默认表** —— 原因见下。
    #
    # 三条实测结论（2026-10-21，用 Work/base 的真实 32 层跑 build() 验证）：
    # 1) targets 里的 ``eyeball_l`` / ``eye_left`` 与分层阶段实际产出的层名
    #    （``eyewhite_left`` / ``irides_left`` / ``eyelid_left``）不匹配，
    #    ``_matched_targets`` 恒为空 —— 真实流水线里这两条 rotation 变形器 **0 个**。
    # 2) pivot(-35,-40) 取自 ``_DEFAULT_BONE_POSITIONS`` 的**比例兜底**值，不是
    #    图像坐标；真实眼骨经质心换算后并不在这个点上（见 LIMITATIONS §3
    #    「骨骼枢轴必须是图像坐标」）。
    # 3) 即便前两条成立，``moc3_pipeline._driving_rotation_parameter`` 也只在
    #    ``ROTATION_KEYFORM_PARAMS``（Head / Body / 双臂 / 三束头发）里找 Z 轴
    #    参数，而眼球对应的是 ParamEyeBallX/Y（平移，不是旋转），永远绑不上。
    #
    # 眼球跟随实际由 ``moc3_pipeline.eye_track_keyforms`` 用 ParamEyeBallX/Y 双轴
    # 网格键形实现 —— 效果不缺，缺的是「这两条不是 rotation 变形器」这件事被说清楚。
    #
    #: 头部刚体侧倾：绕 Head 关节转 ParamAngleZ 的 rotation 定义。
    #:
    #: **已进默认表**。它没有直接成员网格（``allow_empty_targets``）：转的是整棵
    #: HeadRig 子树。曾因「rotation 与 warp 不能互相嵌套」而撤下，那条结论是探针
    #: 口径错误 —— 2026-11-10 用官方 Core 重测：warp 挂在 rotation 之下照常生效，
    #: rotation 挂在 warp 之下只需按「父 warp 静止格 min 平移 + origin 用父 warp 的
    #: (s,t)」写出，静止帧与 ±30° 都逐字节命中（``probe_head_rot_chain2.py``）。
    HEAD_ROTATION: Dict = {
        "name": "HeadRotationZ", "parent": "Head", "type": "rotation",
        "pivot_bone": "Head", "under": "BodySway",
        "allow_empty_targets": True, "targets": [],
    }

    DEFAULT_ROTATION_DEFORMERS: List[Dict] = [
        HEAD_ROTATION,
        {"name": "EyeTrack_L",     "parent": "Eye_L",  "pivot": (-35, -40),
         "type": "rotation", "targets": ["eyeball_l", "eye_left"]},
        {"name": "EyeTrack_R",     "parent": "Eye_R",  "pivot": (35, -40),
         "type": "rotation", "targets": ["eyeball_r", "eye_right"]},
    ]

    def __init__(self) -> None:
        self._deformers: List[Dict] = []
        self._by_name: Dict[str, Dict] = {}

    def build(
        self,
        bone_tree: Dict,
        meshes: Dict,
        layer_names: Optional[List[str]] = None,
    ) -> Dict:
        """Create warp/rotation deformers for all relevant parts.

        Args:
            bone_tree: Output of :meth:`BoneHierarchy.build`.
            meshes:    Dict of mesh dicts (used to infer deformer bounds).
            layer_names: Optional override of available layer names.

        Returns:
            dict with ``deformers`` (list) and ``by_name`` (dict).
        """
        available = set(layer_names or [])
        if not available and meshes:
            available = set(meshes.keys())
        # 必须与 ``_normalise`` 同一套规则（含去序号前缀）：骨骼查找一直在用它，
        # 这里曾自写一份只做大小写的，于是 PSD 导入的 ``000_hair_back`` 一个目标
        # 都命中不了 —— 实测同一批图 11 个 warp 变形器 → 0 个，构建照样报 valid。
        available_norm = {normalise_layer_name(n) for n in available}
        # 归一后的层名 -> 原始写法：关键词命中后要把原始名交回下游（下游按
        # 原始层名找网格）。
        original_by_norm: Dict[str, str] = {}
        for name in sorted(available):
            original_by_norm.setdefault(normalise_layer_name(name), name)
        # 一块网格只能挂一个变形器：先声明的先占用，后面的关键词不再抢。
        claimed: set = set()

        def _matched_targets(spec: Dict) -> List[str]:
            """目标里真的存在的层名（**大小写不敏感**），并支持关键词扩展。

            以前这里拿原始目标去比 ``available_norm``（已小写化），于是
            ``arm_L`` / ``sleeve_L`` 这种带大写侧别的目标永远命中不了，整个变形器
            被静默跳过——实测肩部抬落的变形器因此根本没建出来。
            这里保留原始写法返回，下游 ``build_deformers`` 用 ``_norm`` 匹配网格。

            ``target_keywords`` / ``exclude_keywords`` 是给不同分层器的命名兜底的：
            只写死 ``hair_back`` 时，K-means 的 ``头发_后``、PSD 的 ``000_hair``
            一个都命中不了。关键词命中后仍然返回**原始层名**。
            """
            matched: List[str] = []
            for target in spec["targets"]:
                norm = normalise_layer_name(target)
                if norm in available_norm and norm not in claimed:
                    matched.append(target)
                    claimed.add(norm)

            keywords = spec.get("target_keywords") or ()
            exclusions = spec.get("exclude_keywords") or ()
            if keywords:
                for norm, original in original_by_norm.items():
                    if norm in claimed:
                        continue
                    if exclusions and matches_keywords(original, exclusions):
                        continue
                    if matches_keywords(original, keywords):
                        matched.append(original)
                        claimed.add(norm)
            return matched

        self._deformers = []
        self._by_name = {}

        for spec in self.DEFAULT_WARP_DEFORMERS:
            targets = _matched_targets(spec)
            if not targets:
                continue
            d = self.create_warp_deformer(
                name=spec["name"],
                parent=spec["parent"],
                grid_rows=spec["rows"],
                grid_cols=spec["cols"],
                targets=targets,
                parameters=list(spec.get("parameters") or
                                ([spec["parameter"]] if spec.get("parameter") else [])),
                profile=spec.get("profile", ""),
                mirror=bool(spec.get("mirror", False)),
                # 变形器父级（不是骨骼）：不在这里透传，作者写的 under 就到此为止
                under=spec.get("under", ""),
                axis_layer=spec.get("axis_layer", ""),
            )
            self._deformers.append(d)
            self._by_name[d["name"]] = d

        rotation_skipped: List[Dict] = []
        for spec in self.DEFAULT_ROTATION_DEFORMERS:
            targets = _matched_targets(spec)
            # 纯父级 rotation（如 HeadRotationZ）没有直接成员：它靠 under 链
            # 旋转整棵子树，targets 为空是正常状态，不算「未命中」。
            if not targets and not spec.get("allow_empty_targets"):
                # 不许静默跳过：targets 与真实分层产出长期不匹配（见
                # DEFAULT_ROTATION_DEFORMERS 的说明）。静默会让「模型里没有
                # rotation 变形器」被误读成「rotation 已支持但没绑参数」，
                # 两者的修法完全不同，必须留下可查的原因。
                rotation_skipped.append({
                    "name": spec["name"],
                    "reason": "目标层名未命中",
                    "targets": list(spec["targets"]),
                    "available_sample": sorted(available_norm)[:6],
                })
                continue
            d = self.create_rotation_deformer(
                name=spec["name"],
                parent=spec["parent"],
                pivot=spec.get("pivot", (0.0, 0.0)),
                targets=targets,
                under=spec.get("under", ""),
                pivot_bone=spec.get("pivot_bone", ""),
            )
            self._deformers.append(d)
            self._by_name[d["name"]] = d

        if rotation_skipped:
            log.warning(
                "rotation 变形器未创建 %d 个（目标层名与分层产出不匹配）：%s。"
                "眼球/头部转动由网格键形承担，见 docs/LIMITATIONS.md §3。"
                % (len(rotation_skipped),
                   [d["name"] for d in rotation_skipped]))

        log.info(f"Created {len(self._deformers)} deformers")
        return {"deformers": self._deformers, "by_name": self._by_name,
                "rotation_skipped": rotation_skipped}

    def create_warp_deformer(
        self,
        name: str,
        parent: str,
        grid_rows: int = 2,
        grid_cols: int = 2,
        targets: Optional[List[str]] = None,
        parameters: Optional[List[str]] = None,
        profile: str = "",
        mirror: bool = False,
        under: str = "",
        axis_layer: str = "",
    ) -> Dict:
        """Create a warp (free-form) deformer with an R*C control grid.

        Args:
            name:      Deformer identifier.
            parent:    骨骼名（Part/Bone 归属，写进 model3.json 那一层）。
            grid_rows: Number of control point rows.
            grid_cols: Number of control point columns.
            targets:   Layer names this deformer affects.
            parameters: 显式驱动参数 id 列表（按 profile 的角色顺序）。空表示不显式
                       指定，留给导出阶段按枢轴推导（呼吸曾经就是这么绑的）。
            profile:   形变方式，取值见 ``moc3_pipeline.DEFORMER_PROFILES``。
            mirror:    左右对称部件的镜像标记（眉毛倾斜方向取反）。
            under:     **变形器**父级名（不是骨骼）。导出阶段
                       （``moc3_pipeline.resolve_deformer_parents``）把它解析成
                       moc3 的 ``parent_deformer_id``，于是这一级继承父级的转动，
                       不必再各拟合一份。空 = 根级。
                       这个键必须在这里显式接住：下面的 ``out`` 是**固定键集**重建
                       的字典，作者在 ``DEFAULT_WARP_DEFORMERS`` 写的 ``under``
                       不接就会被静默丢掉。

        Returns:
            Deformer definition dict.
        """
        rows = max(2, grid_rows)
        cols = max(2, grid_cols)
        # Default control grid: identity points on a 1x1 area
        points = []
        for r in range(rows):
            for c in range(cols):
                points.append([
                    round(c / (cols - 1), 4),
                    round(r / (rows - 1), 4),
                ])
        out = {
            "name": name,
            "type": "warp",
            "parent": parent,
            "grid_rows": rows,
            "grid_cols": cols,
            "control_points": points,
            "targets": list(targets or []),
            "base_points": [list(p) for p in points],  # rest position
        }
        if parameters:
            out["parameters"] = list(parameters)
        if profile:
            out["profile"] = profile
        if mirror:
            out["mirror"] = True
        if under:
            # 没有 under 就不冒出这个键：条目字典的形状保持今天的样子
            out["under"] = under
        if axis_layer:
            # 二维颈胸场的参照层（见 moc3_pipeline.neck_band_side_weight）：
            # 同样必须在这里显式接住，否则 ``out`` 的固定键集会把作者写的丢静默。
            out["axis_layer"] = axis_layer
        return out

    def create_rotation_deformer(
        self,
        name: str,
        parent: str,
        pivot: Tuple[float, float] = (0.0, 0.0),
        angle: float = 0.0,
        targets: Optional[List[str]] = None,
        under: str = "",
        pivot_bone: str = "",
    ) -> Dict:
        """Create a rotation deformer pivoting around ``pivot``.

        Args:
            name:   Deformer identifier.
            parent: 骨骼名（Part/Bone 归属）。
            pivot:  (x, y) pivot point in image space.
            angle:  Default rotation in degrees.
            targets: Layer names affected；为空表示只靠 ``under`` 链作用整棵子树。
            under:  **变形器**父级名，语义同 :meth:`create_warp_deformer`
                    （导出阶段解析成 ``parent_deformer_id``）。
            pivot_bone: 枢轴取该骨骼的实际位置（图像坐标），覆盖 ``pivot``。
                    Head 这类关节位置只有运行时才知道，作者表写不出来。

        Returns:
            Deformer definition dict.
        """
        out = {
            "name": name,
            "type": "rotation",
            "parent": parent,
            "pivot": [float(pivot[0]), float(pivot[1])],
            "angle": float(angle),
            "targets": list(targets or []),
        }
        if pivot_bone:
            out["pivot_bone"] = pivot_bone
        if under:
            out["under"] = under
        return out

    def to_json(self) -> Dict:
        """Export all deformers as a JSON-serialisable dict (Cubism import format)."""
        return {
            "version": 1,
            "deformers": [
                {
                    "name": d["name"],
                    "type": d["type"],
                    "parent": d["parent"],
                    **(
                        {
                            "grid_rows": d.get("grid_rows", 2),
                            "grid_cols": d.get("grid_cols", 2),
                            "control_points": d.get("control_points", []),
                        }
                        if d["type"] == "warp"
                        else {
                            "pivot": d.get("pivot", [0, 0]),
                            "angle": d.get("angle", 0.0),
                        }
                    ),
                    "targets": d.get("targets", []),
                }
                for d in self._deformers
            ],
        }
