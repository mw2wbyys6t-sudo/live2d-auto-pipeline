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
        # ---- 配件 ----
        "accessories": "Torso",
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

        inferred = self._infer_from_centroid(centroid, span)
        if inferred:
            return inferred, "position"
        return self.DEFAULT_BONE, "default"

    def _infer_from_centroid(
        self,
        centroid: Optional[Tuple[float, float]],
        span: Optional[Tuple[float, float, float, float]],
    ) -> Optional[str]:
        """按质心落在身体的哪一段猜骨骼。

        比例取七头身立绘的经验值：头 0~20%、肩胸 20~34%、躯干 34~50%、
        腰胯 50~68%、腿 68% 以下。猜错也只是"衣服跟躯干走"这种可接受的
        近似，远好过以前一律挂到 ``Face`` 上。
        """
        if centroid is None or span is None:
            return None
        x0, y0, x1, y1 = span
        height = max(1.0, y1 - y0)
        frac = (float(centroid[1]) - y0) / height
        if frac < 0.20:
            return "Head"
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
            "Leg_L":         (-30.0, 240.0),
            "Calf_L":        (-30.0, 340.0),
            "Foot_L":        (-30.0, 440.0),
            "Leg_R":         ( 30.0, 240.0),
            "Calf_R":        ( 30.0, 340.0),
            "Foot_R":        ( 30.0, 440.0),
        }

    @staticmethod
    def _normalise(name: str) -> str:
        """Normalise a layer name for lookup.

        lower-case + separators to underscores, and strip a leading numeric
        index prefix. Layer PNGs are exported as ``NNN_part.png``
        (``000_arms``, ``005_face``), so without the prefix strip every
        lookup falls through to the default bone and all layers end up
        parented to ``Face`` — which silently disables parameter keyforms.
        """
        norm = name.strip().lower().replace(" ", "_").replace("-", "_")
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

    DEFAULT_WARP_DEFORMERS: List[Dict] = [
        # ---- 头发摆动：由 ParamHairSwing 驱动，发根钉住、发梢摆 ----
        {"name": "HairFrontSwing", "parent": "Head", "rows": 3, "cols": 3,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamHairSwing"],
         "targets": ["hair_front", "hair_bangs", "hair_mid", "hair_side"]},
        {"name": "HairBackSwing",  "parent": "Head", "rows": 4, "cols": 2,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamHairSwing"],
         "targets": ["hair_back", "hair_shadow_back"]},
        {"name": "HairSideSwing_L","parent": "Head", "rows": 2, "cols": 2,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamHairSwing"],
         "targets": ["hair_back_left"]},
        {"name": "HairSideSwing_R","parent": "Head", "rows": 2, "cols": 2,
         "type": "warp", "profile": "hair_sway",
         "parameters": ["ParamHairSwing"],
         "targets": ["hair_back_right"]},
        # ---- 躯干：侧倾 / 前后倾 / 摆动 / 呼吸（胯部不动，越靠肩位移越大）----
        {"name": "BodySway",       "parent": "Torso", "rows": 3, "cols": 2,
         "type": "warp", "profile": "torso",
         "parameters": ["ParamBodyAngleX", "ParamBodyAngleY",
                        "ParamBodySway", "ParamBreath"],
         "targets": ["clothes_top", "clothes_inner", "clothes_outer",
                     "waistband", "collar", "chest", "body"]},
        {"name": "SkirtSway",      "parent": "Skirt", "rows": 4, "cols": 3,
         "type": "warp", "targets": ["skirt"]},
        # ---- 表情形变：参数写死在说明里（这些效果没有对应骨骼，无从推导）----
        #
        # 放在变形器层而不是网格键形层，是因为网格只能挂**一个**参数带：眼睛网格
        # 已经用双眼球键形（ParamEyeBallX/Y）、嘴与眉已经用头部三轴键形
        # （ParamAngleX/Y/Z）；变形器与网格键形是两层，可以同时生效。
        # 一个变形器也只能挂一个带，所以同一部件的多个参数合成一条**多轴带**
        # （见 moc3_pipeline.DEFORMER_PROFILES / moc3_model.WarpDeformerSpec.axes）。
        {"name": "MouthOpen", "parent": "Mouth", "rows": 2, "cols": 3,
         "type": "warp", "profile": "mouth",
         "parameters": ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthSize"],
         "targets": ["mouth", "mouth_open", "mouth_close"]},
        # 口内：与嘴线同源、但**张得更深**，所以在嘴唇下方露出一条腔体。
        # 若挂在 MouthOpen 下（同样的 2.5×），它会永远正好被嘴唇盖住——等于没做。
        {"name": "MouthCavity", "parent": "Mouth", "rows": 2, "cols": 2,
         "type": "warp", "profile": "mouth_cavity",
         "parameters": ["ParamMouthOpenY"],
         "targets": ["mouth_cavity", "mouth_inner"]},
        # 闭眼：眼球整体**向上收拢**进上睫毛线里（``eye`` profile 的轴心取眼睛
        # 轮廓上沿）。眼睛下面已经画好了「闭眼图」（脸层肤色 + 原画上睫毛，见
        # LayerComposer._synthesize_eyelid），收拢后露出来就是闭眼的样子。
        # 闭眼图是**静态**的，所以不占变形器：它由 EyeBlink 的收拢动作"让出来"。
        {"name": "EyeBlink_L", "parent": "Eye_L", "rows": 2, "cols": 2,
         "type": "warp", "profile": "eye",
         "parameters": ["ParamEyeLOpen"],
         "targets": ["eyewhite_left", "irides_left", "eyelash_left",
                     "eyes_left", "eye_left"]},
        {"name": "EyeBlink_R", "parent": "Eye_R", "rows": 2, "cols": 2,
         "type": "warp", "profile": "eye",
         "parameters": ["ParamEyeROpen"],
         "targets": ["eyewhite_right", "irides_right", "eyelash_right",
                     "eyes_right", "eye_right"]},
        {"name": "BrowMove_L", "parent": "Brow_L", "rows": 2, "cols": 3,
         "type": "warp", "profile": "brow",
         "parameters": ["ParamBrowLY", "ParamBrowLAngle", "ParamBrowLForm"],
         "targets": ["eyebrow_L", "brow_L", "brow_left"]},
        {"name": "BrowMove_R", "parent": "Brow_R", "rows": 2, "cols": 3,
         "type": "warp", "profile": "brow", "mirror": True,
         "parameters": ["ParamBrowRY", "ParamBrowRAngle", "ParamBrowRForm"],
         "targets": ["eyebrow_R", "brow_R", "brow_right"]},
        # ---- 肩部抬落 ----
        #
        # 没有独立的肩部图层，只能抬整条上臂（Live2D 里肩参数本来也会带上臂）。
        # 幅度刻意小（上臂高度的 12%），避免看起来像"举手"。
        {"name": "ShoulderLift_L", "parent": "ArmBack_L", "rows": 2, "cols": 2,
         "type": "warp", "profile": "lift",
         "parameters": ["ParamShoulderL"],
         "targets": ["arm_L", "upper_arm_back_left", "sleeve_L"]},
        {"name": "ShoulderLift_R", "parent": "ArmBack_R", "rows": 2, "cols": 2,
         "type": "warp", "profile": "lift",
         "parameters": ["ParamShoulderR"],
         "targets": ["arm_R", "upper_arm_back_right", "sleeve_R"]},
    ]

    # ---- rotation 变形器：当前**不会**被创建，保留定义只为标明意图 ----
    #
    # 三条实测结论（2026-10-21，用 Work/base 的真实 32 层跑 build() 验证）：
    # 1) targets 里的 ``eyeball_l`` / ``eye_left`` 与分层阶段实际产出的层名
    #    （``eyewhite_left`` / ``irides_left`` / ``eyelid_left``）不匹配，
    #    ``_matched_targets`` 恒为空 —— 真实流水线里 rotation 变形器 **0 个**。
    # 2) pivot(-35,-40) 取自 ``_DEFAULT_BONE_POSITIONS`` 的**比例兜底**值，不是
    #    图像坐标；真实眼骨经质心换算后并不在这个点上（见 LIMITATIONS §3
    #    「骨骼枢轴必须是图像坐标」）。
    # 3) 即便前两条成立，``moc3_pipeline._driving_rotation_parameter`` 也只在
    #    ``ROTATION_KEYFORM_PARAMS``（Head / Body / 双臂 / 三束头发）里找 Z 轴
    #    参数，而眼球对应的是 ParamEyeBallX/Y（平移，不是旋转），永远绑不上。
    #
    # 眼球跟随实际由 ``moc3_pipeline.eye_track_keyforms`` 用 ParamEyeBallX/Y 双轴
    # 网格键形实现，头部三轴转动由 ``head_angle_keyforms`` 实现 —— 效果不缺，
    # 缺的是「这两条不是 rotation 变形器」这件事被说清楚。
    # 要让 rotation 变形器真正投入使用，必须换一个不被 warp 变形器占用、且语义
    # 确实是刚体旋转的部件（例如手臂绕肩/绕肘），并同步调整网格归属。那是美术
    # 决策，不能靠推断代替。
    DEFAULT_ROTATION_DEFORMERS: List[Dict] = [
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
        available_norm = {n.strip().lower().replace(" ", "_") for n in available}

        def _matched_targets(spec: Dict) -> List[str]:
            """目标里真的存在的层名（**大小写不敏感**）。

            以前这里拿原始目标去比 ``available_norm``（已小写化），于是
            ``arm_L`` / ``sleeve_L`` 这种带大写侧别的目标永远命中不了，整个变形器
            被静默跳过——实测肩部抬落的变形器因此根本没建出来。
            这里保留原始写法返回，下游 ``build_deformers`` 用 ``_norm`` 匹配网格。
            """
            return [t for t in spec["targets"]
                    if t.strip().lower().replace(" ", "_") in available_norm]

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
            )
            self._deformers.append(d)
            self._by_name[d["name"]] = d

        rotation_skipped: List[Dict] = []
        for spec in self.DEFAULT_ROTATION_DEFORMERS:
            targets = _matched_targets(spec)
            if not targets:
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
                pivot=spec["pivot"],
                targets=targets,
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
    ) -> Dict:
        """Create a warp (free-form) deformer with an R*C control grid.

        Args:
            name:      Deformer identifier.
            parent:    Parent deformer or bone name.
            grid_rows: Number of control point rows.
            grid_cols: Number of control point columns.
            targets:   Layer names this deformer affects.
            parameters: 显式驱动参数 id 列表（按 profile 的角色顺序）。空表示不显式
                       指定，留给导出阶段按枢轴推导（呼吸曾经就是这么绑的）。
            profile:   形变方式，取值见 ``moc3_pipeline.DEFORMER_PROFILES``。
            mirror:    左右对称部件的镜像标记（眉毛倾斜方向取反）。

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
        return out

    def create_rotation_deformer(
        self,
        name: str,
        parent: str,
        pivot: Tuple[float, float] = (0.0, 0.0),
        angle: float = 0.0,
        targets: Optional[List[str]] = None,
    ) -> Dict:
        """Create a rotation deformer pivoting around ``pivot``.

        Args:
            name:   Deformer identifier.
            parent: Parent deformer or bone name.
            pivot:  (x, y) pivot point in model space.
            angle:  Default rotation in degrees.
            targets: Layer names affected.

        Returns:
            Deformer definition dict.
        """
        return {
            "name": name,
            "type": "rotation",
            "parent": parent,
            "pivot": [float(pivot[0]), float(pivot[1])],
            "angle": float(angle),
            "targets": list(targets or []),
        }

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
