#!/usr/bin/env python3
"""
Live2D Master Agent - 52-Layer Standard Structure Mapper (DEF-004 IMPLEMENTED)

DEF-004: Maps K-means or manually separated layers to the official
Live2D Cubism 52-layer standard, and generates parameter/physics configuration.

This implements the full 52-layer Live2D standard with:
- Standard Chinese naming (back-to-front draw order)
- Part type classification
- Parameter configuration generation (angle, eye, mouth, etc.)
- Physics configuration generation (hair swing, body bounce, breath)
- Export-ready guide for Cubism Editor import
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

from core.logger import get_logger

log = get_logger("layers52")


# Official 52-layer Live2D standard (back-to-front draw order)
LIVE2D_52_LAYERS: List[Dict] = [
    # === Background ===
    {"id": 0, "name_cn": "背景", "name_en": "Background", "group": "bg", "draw_order": 0, "required": False},

    # === Back Hair (后发) ===
    {"id": 1, "name_cn": "头发_后", "name_en": "Hair_Back", "group": "hair_back", "draw_order": 10, "required": True},
    {"id": 2, "name_cn": "头发_阴影_后", "name_en": "Hair_Shadow_Back", "group": "hair_back", "draw_order": 11, "required": False},
    {"id": 3, "name_cn": "头发_后_左", "name_en": "Hair_Back_Left", "group": "hair_back", "draw_order": 12, "required": False},
    {"id": 4, "name_cn": "头发_后_右", "name_en": "Hair_Back_Right", "group": "hair_back", "draw_order": 13, "required": False},

    # === Body (back) ===
    {"id": 5, "name_cn": "脖子", "name_en": "Neck", "group": "body", "draw_order": 20, "required": True},
    {"id": 6, "name_cn": "胸腔", "name_en": "Chest", "group": "body", "draw_order": 21, "required": True},
    {"id": 7, "name_cn": "腰臀", "name_en": "Waist_Hips", "group": "body", "draw_order": 22, "required": True},

    # === Legs ===
    {"id": 8, "name_cn": "大腿_左", "name_en": "Thigh_Left", "group": "legs", "draw_order": 30, "required": True},
    {"id": 9, "name_cn": "大腿_右", "name_en": "Thigh_Right", "group": "legs", "draw_order": 31, "required": True},
    {"id": 10, "name_cn": "小腿_左", "name_en": "Calf_Left", "group": "legs", "draw_order": 32, "required": False},
    {"id": 11, "name_cn": "小腿_右", "name_en": "Calf_Right", "group": "legs", "draw_order": 33, "required": False},
    {"id": 12, "name_cn": "脚_左", "name_en": "Foot_Left", "group": "legs", "draw_order": 34, "required": False},
    {"id": 13, "name_cn": "脚_右", "name_en": "Foot_Right", "group": "legs", "draw_order": 35, "required": False},

    # === Back Arms ===
    {"id": 14, "name_cn": "上臂_后_左", "name_en": "UpperArm_Back_Left", "group": "arms_back", "draw_order": 40, "required": False},
    {"id": 15, "name_cn": "上臂_后_右", "name_en": "UpperArm_Back_Right", "group": "arms_back", "draw_order": 41, "required": False},
    {"id": 16, "name_cn": "前臂_后_左", "name_en": "Forearm_Back_Left", "group": "arms_back", "draw_order": 42, "required": False},
    {"id": 17, "name_cn": "前臂_后_右", "name_en": "Forearm_Back_Right", "group": "arms_back", "draw_order": 43, "required": False},
    {"id": 18, "name_cn": "手_后_左", "name_en": "Hand_Back_Left", "group": "arms_back", "draw_order": 44, "required": False},
    {"id": 19, "name_cn": "手_后_右", "name_en": "Hand_Back_Right", "group": "arms_back", "draw_order": 45, "required": False},

    # === Clothes ===
    {"id": 20, "name_cn": "衣服_内层", "name_en": "Clothes_Inner", "group": "clothes", "draw_order": 50, "required": False},
    {"id": 21, "name_cn": "衣服_外层", "name_en": "Clothes_Outer", "group": "clothes", "draw_order": 51, "required": True},
    {"id": 22, "name_cn": "配饰", "name_en": "Accessories", "group": "clothes", "draw_order": 52, "required": False},

    # === Face Base ===
    {"id": 23, "name_cn": "脸_基础", "name_en": "Face_Base", "group": "face", "draw_order": 60, "required": True},
    {"id": 24, "name_cn": "脸_腮红", "name_en": "Face_Blush", "group": "face", "draw_order": 61, "required": False},

    # === Ears ===
    {"id": 25, "name_cn": "耳朵_左", "name_en": "Ear_Left", "group": "ears", "draw_order": 65, "required": False},
    {"id": 26, "name_cn": "耳朵_右", "name_en": "Ear_Right", "group": "ears", "draw_order": 66, "required": False},

    # === Nose ===
    {"id": 27, "name_cn": "鼻子", "name_en": "Nose", "group": "nose", "draw_order": 70, "required": False},

    # === Mouth ===
    {"id": 28, "name_cn": "口腔", "name_en": "Mouth_Cavity", "group": "mouth", "draw_order": 75, "required": True},
    {"id": 29, "name_cn": "舌头", "name_en": "Mouth_Tongue", "group": "mouth", "draw_order": 76, "required": False},
    {"id": 30, "name_cn": "牙齿", "name_en": "Mouth_Teeth", "group": "mouth", "draw_order": 77, "required": False},
    {"id": 31, "name_cn": "下唇", "name_en": "Mouth_LowerLip", "group": "mouth", "draw_order": 78, "required": True},
    {"id": 32, "name_cn": "上唇", "name_en": "Mouth_UpperLip", "group": "mouth", "draw_order": 79, "required": True},

    # === Eyes (right then left from viewer perspective) ===
    {"id": 33, "name_cn": "眼白_右", "name_en": "EyeWhite_Right", "group": "eyes", "draw_order": 85, "required": True},
    {"id": 34, "name_cn": "眼白_左", "name_en": "EyeWhite_Left", "group": "eyes", "draw_order": 86, "required": True},
    {"id": 35, "name_cn": "虹膜_右", "name_en": "Iris_Right", "group": "eyes", "draw_order": 87, "required": True},
    {"id": 36, "name_cn": "虹膜_左", "name_en": "Iris_Left", "group": "eyes", "draw_order": 88, "required": True},
    {"id": 37, "name_cn": "瞳孔_右", "name_en": "Pupil_Right", "group": "eyes", "draw_order": 89, "required": True},
    {"id": 38, "name_cn": "瞳孔_左", "name_en": "Pupil_Left", "group": "eyes", "draw_order": 90, "required": True},
    {"id": 39, "name_cn": "高光_右", "name_en": "Highlight_Right", "group": "eyes", "draw_order": 91, "required": True},
    {"id": 40, "name_cn": "高光_左", "name_en": "Highlight_Left", "group": "eyes", "draw_order": 92, "required": True},

    # === Eyelashes ===
    {"id": 41, "name_cn": "睫毛_上_右", "name_en": "Lash_Upper_Right", "group": "eyelashes", "draw_order": 95, "required": True},
    {"id": 42, "name_cn": "睫毛_上_左", "name_en": "Lash_Upper_Left", "group": "eyelashes", "draw_order": 96, "required": True},
    {"id": 43, "name_cn": "睫毛_下_右", "name_en": "Lash_Lower_Right", "group": "eyelashes", "draw_order": 97, "required": False},
    {"id": 44, "name_cn": "睫毛_下_左", "name_en": "Lash_Lower_Left", "group": "eyelashes", "draw_order": 98, "required": False},

    # === Eyebrows ===
    {"id": 45, "name_cn": "眉毛_右", "name_en": "Eyebrow_Right", "group": "eyebrows", "draw_order": 100, "required": True},
    {"id": 46, "name_cn": "眉毛_左", "name_en": "Eyebrow_Left", "group": "eyebrows", "draw_order": 101, "required": True},

    # === Front Hair (前发) ===
    {"id": 47, "name_cn": "侧发_右", "name_en": "SideHair_Right", "group": "hair_front", "draw_order": 110, "required": True},
    {"id": 48, "name_cn": "侧发_左", "name_en": "SideHair_Left", "group": "hair_front", "draw_order": 111, "required": True},
    {"id": 49, "name_cn": "刘海", "name_en": "Bangs", "group": "hair_front", "draw_order": 112, "required": True},
    {"id": 50, "name_cn": "呆毛", "name_en": "Ahoge", "group": "hair_front", "draw_order": 113, "required": False},
    {"id": 51, "name_cn": "头发_高光_前", "name_en": "Hair_Highlight_Front", "group": "hair_front", "draw_order": 114, "required": False},
]

# Standard Live2D parameter definitions.
#
# The "groups" key matches the schema used by
# :mod:`live2d_builder.blendshapes.parameters` (``STANDARD_PARAMETERS``).
STANDARD_PARAMS: List[Dict] = [
    # --- Head/Body Angle ---
    {"id": "ParamAngleX", "name": "角度X", "min": -30, "max": 30, "default": 0, "groups": ["head", "hair_front", "hair_back"]},
    {"id": "ParamAngleY", "name": "角度Y", "min": -30, "max": 30, "default": 0, "groups": ["head", "hair_front"]},
    {"id": "ParamAngleZ", "name": "角度Z", "min": -30, "max": 30, "default": 0, "groups": ["head", "hair_front", "hair_back"]},
    {"id": "ParamBodyAngleX", "name": "身体旋转X", "min": -10, "max": 10, "default": 0, "groups": ["body", "clothes", "arms_front"]},
    {"id": "ParamBodyAngleY", "name": "身体旋转Y", "min": -10, "max": 10, "default": 0, "groups": ["body", "clothes"]},

    # --- Eyes ---
    {"id": "ParamEyeLOpen", "name": "左眼开闭", "min": 0, "max": 1, "default": 1, "groups": ["eyes"]},
    {"id": "ParamEyeROpen", "name": "右眼开闭", "min": 0, "max": 1, "default": 1, "groups": ["eyes"]},
    {"id": "ParamEyeLSmile", "name": "左眼笑", "min": 0, "max": 1, "default": 0, "groups": ["eyes", "eyelashes"]},
    {"id": "ParamEyeRSmile", "name": "右眼笑", "min": 0, "max": 1, "default": 0, "groups": ["eyes", "eyelashes"]},
    {"id": "ParamEyeBallX", "name": "眼球X", "min": -1, "max": 1, "default": 0, "groups": ["eyes"]},
    {"id": "ParamEyeBallY", "name": "眼球Y", "min": -1, "max": 1, "default": 0, "groups": ["eyes"]},

    # --- Mouth ---
    {"id": "ParamMouthOpenY", "name": "口开闭", "min": 0, "max": 1, "default": 0, "groups": ["mouth"]},
    {"id": "ParamMouthForm", "name": "口形状", "min": -1, "max": 1, "default": 0, "groups": ["mouth"]},

    # --- Eyebrows ---
    {"id": "ParamBrowLY", "name": "左眉上下", "min": -1, "max": 1, "default": 0, "groups": ["eyebrows"]},
    {"id": "ParamBrowRY", "name": "右眉上下", "min": -1, "max": 1, "default": 0, "groups": ["eyebrows"]},
    {"id": "ParamBrowLAngle", "name": "左眉角度", "min": -1, "max": 1, "default": 0, "groups": ["eyebrows"]},
    {"id": "ParamBrowRAngle", "name": "右眉角度", "min": -1, "max": 1, "default": 0, "groups": ["eyebrows"]},

    # --- Breathing ---
    {"id": "ParamBreath", "name": "呼吸", "min": 0, "max": 1, "default": 0.5, "groups": ["body", "clothes", "shoulders"]},

    # --- Hair Physics ---
    {"id": "ParamHairBackX", "name": "后发X", "min": -15, "max": 15, "default": 0, "groups": ["hair_back"]},
    {"id": "ParamHairFrontX", "name": "前发X", "min": -15, "max": 15, "default": 0, "groups": ["hair_front"]},
    {"id": "ParamHairSideX", "name": "侧发X", "min": -15, "max": 15, "default": 0, "groups": ["hair_front"]},
]

# Physics configuration (for physics3.json)
STANDARD_PHYSICS: Dict = {
    "version": 3,
    "meta": {"setting_count": 4, "physics_setting_count": 0},
    "physics_settings": [
        {
            "id": "HairFront",
            "name": "前发摇摆",
            "input": [
                {"source": {"target": "Parameter", "id": "ParamAngleX"}, "weight": 10},
                {"source": {"target": "Parameter", "id": "ParamBodyAngleX"}, "weight": 5},
            ],
            "output": [
                {"destination": {"target": "Parameter", "id": "ParamHairFrontX"}, "weight": 100, "scale": 1.0},
                {"destination": {"target": "Parameter", "id": "ParamHairSideX"}, "weight": 70, "scale": 0.8},
            ],
            "pendulums": [
                {"length": 0.3, "damping": 0.85, "stiffness": 0.3, "mass": 1.0},
                {"length": 0.5, "damping": 0.9, "stiffness": 0.2, "mass": 0.8},
            ],
            "fps": 60,
        },
        {
            "id": "HairBack",
            "name": "后发摇摆",
            "input": [
                {"source": {"target": "Parameter", "id": "ParamAngleX"}, "weight": 8},
                {"source": {"target": "Parameter", "id": "ParamBodyAngleX"}, "weight": 8},
            ],
            "output": [
                {"destination": {"target": "Parameter", "id": "ParamHairBackX"}, "weight": 100, "scale": 1.0},
            ],
            "pendulums": [
                {"length": 0.8, "damping": 0.92, "stiffness": 0.15, "mass": 1.2},
                {"length": 1.0, "damping": 0.95, "stiffness": 0.1, "mass": 1.0},
                {"length": 1.2, "damping": 0.97, "stiffness": 0.08, "mass": 0.8},
            ],
            "fps": 60,
        },
        {
            "id": "BodyBounce",
            "name": "身体弹跳",
            "input": [
                {"source": {"target": "Parameter", "id": "ParamBodyAngleY"}, "weight": 10},
                {"source": {"target": "Parameter", "id": "ParamAngleY"}, "weight": 5},
            ],
            "output": [
                {"destination": {"target": "Parameter", "id": "ParamBreath"}, "weight": 30, "scale": 0.5},
            ],
            "pendulums": [
                {"length": 0.2, "damping": 0.7, "stiffness": 0.5, "mass": 1.5},
            ],
            "fps": 60,
        },
        {
            "id": "Breathing",
            "name": "呼吸",
            "input": [
                {"source": {"target": "Parameter", "id": "ParamBreath"}, "weight": 1},
            ],
            "output": [
                {"destination": {"target": "Parameter", "id": "ParamBodyAngleY"}, "weight": 50, "scale": 0.3},
            ],
            "pendulums": [
                {"length": 2.0, "damping": 0.99, "stiffness": 0.05, "mass": 2.0},
            ],
            "fps": 30,
        },
    ],
}


@dataclass
class Layer52Mapping:
    """Maps an actual layer to a standard 52-layer position."""
    standard_id: int
    standard_name_cn: str
    standard_name_en: str
    group: str
    draw_order: int
    source_file: str = ""
    mapped: bool = False
    notes: str = ""


class Layer52Generator:
    """Generates the 52-layer standard structure with parameter/physics configs (DEF-004)."""

    def __init__(self):
        self.standard_layers = LIVE2D_52_LAYERS
        self.standard_params = STANDARD_PARAMS
        self.physics_config = STANDARD_PHYSICS

    def map_layers_to_standard(
        self,
        layers_info: List[Dict],
    ) -> Dict:
        """Map detected layers to the 52-layer standard.

        Uses heuristic matching based on color, position, and size to assign
        K-means layers to standard positions. Returns a complete mapping plan.
        """
        mappings: List[Layer52Mapping] = []
        used_standard_ids = set()

        # Track which standard layers are "covered" by the available layers
        for std_layer in self.standard_layers:
            mapping = Layer52Mapping(
                standard_id=std_layer["id"],
                standard_name_cn=std_layer["name_cn"],
                standard_name_en=std_layer["name_en"],
                group=std_layer["group"],
                draw_order=std_layer["draw_order"],
            )
            mappings.append(mapping)

        # Assign detected layers to standard positions using keyword matching.
        # Supports both Chinese K-means labels ("头发", "脸") and English
        # semantic segmentation / LayerComposer names ("hair_back", "bangs",
        # "eye_right", "clothes_outer", …).
        part_keywords = {
            # --- Chinese (K-means path) ---
            "头发": ["hair_back", "hair_front"],
            "头发_后": ["hair_back"],
            "刘海": ["hair_front"],
            "皮肤": ["body", "face"],
            "脸": ["face"],
            "眼睛": ["eyes"],
            "眉毛": ["eyebrows"],
            "睫毛": ["eyelashes"],
            "嘴巴": ["mouth"],
            "嘴唇": ["mouth"],
            "衣服": ["clothes"],
            "腮红": ["face"],
            "鼻子": ["nose"],
            "耳朵": ["ears"],
            "高光": ["eyes"],
            "脖子": ["body"],
            # --- English (semantic / LayerComposer path) ---
            "background": ["bg"],
            "scalp": ["hair_back"],
            "hair_back": ["hair_back"],
            "hair_mid": ["hair_back"],
            "hair_front": ["hair_front"],
            "sidehair": ["hair_front"],
            "hair_stray": ["hair_front"],
            "bangs": ["hair_front"],
            "ahoge": ["hair_front"],
            "neck": ["body"],
            "body": ["body"],
            "skin": ["body", "face"],
            "chest": ["body"],
            "clothes_top": ["clothes"],
            "clothes_inner": ["clothes"],
            "clothes_outer": ["clothes"],
            "accessories": ["clothes"],
            "face": ["face"],
            "blush": ["face"],
            "ear": ["ears"],
            "nose": ["nose"],
            "mouth": ["mouth"],
            "lip": ["mouth"],
            "tooth": ["mouth"],
            "teeth": ["mouth"],
            "tongue": ["mouth"],
            "eye": ["eyes"],
            "iris": ["eyes"],
            "pupil": ["eyes"],
            "highlight": ["eyes"],
            "sclera": ["eyes"],
            "eyelash": ["eyelashes"],
            "lash": ["eyelashes"],
            "brow": ["eyebrows"],
            "eyebrow": ["eyebrows"],
        }

        # Fine-grained direct part_name → standard_id mappings (higher priority
        # than keyword → group heuristic). Semantic / LayerComposer names map
        # directly; K-means generic labels fall through to group heuristic.
        direct_map: Dict[str, List[int]] = {
            # English LayerComposer / semantic labels → standard IDs
            "background": [0],
            "scalp": [1],
            "hair_back": [1, 2, 3, 4],
            "hair_mid": [2],
            "hair_front": [51],
            "bangs": [49],
            "sidehair_right": [47],
            "sidehair_left": [48],
            "sidehair": [47, 48],
            "ahoge": [50],
            "hair_stray": [50, 51],
            "hair": [1, 49, 47, 48],
            "neck": [5],
            "body": [6, 7],
            "chest": [6],
            "waist": [7],
            "skin": [23, 5, 6, 7],
            "clothes_inner": [20],
            "clothes_outer": [21],
            "clothes_top": [21],
            "clothes": [21, 20],
            "accessories": [22],
            "face": [23],
            "blush": [24],
            "ear_left": [25],
            "ear_right": [26],
            "ear": [25, 26],
            "nose": [27],
            "mouth": [28, 31, 32],
            "mouth_cavity": [28],
            "tongue": [29],
            "teeth": [30],
            "lower_lip": [31],
            "upper_lip": [32],
            "lip": [31, 32],
            "eye_white_right": [33],
            "eye_white_left": [34],
            "sclera_right": [33],
            "sclera_left": [34],
            "iris_right": [35],
            "iris_left": [36],
            "pupil_right": [37],
            "pupil_left": [38],
            "eye_highlight_right": [39],
            "eye_highlight_left": [40],
            "highlight_right": [39],
            "highlight_left": [40],
            "eye_right": [33, 35, 37, 39, 41, 43],
            "eye_left": [34, 36, 38, 40, 42, 44],
            "eye": [33, 34, 35, 36, 37, 38, 39, 40],
            "eyelash_upper_right": [41],
            "eyelash_upper_left": [42],
            "eyelash_lower_right": [43],
            "eyelash_lower_left": [44],
            "lash_upper_right": [41],
            "lash_upper_left": [42],
            "lash_lower_right": [43],
            "lash_lower_left": [44],
            "eyelash": [41, 42],
            "lash": [41, 42],
            "eyebrow_right": [45],
            "eyebrow_left": [46],
            "brow_right": [45],
            "brow_left": [46],
            "brow": [45, 46],
            "eyebrow": [45, 46],
            # Chinese K-means labels → standard IDs
            "头发_后": [1],
            "头发_中": [2],
            "头发": [1, 49, 47, 48],
            "刘海": [49],
            "呆毛": [50],
            "皮肤": [23, 5, 6, 7],
            "脸": [23],
            "腮红": [24],
            "鼻子": [27],
            "嘴巴": [28, 31, 32],
            "嘴唇": [31, 32],
            "眼睛": [33, 34, 35, 36, 37, 38, 39, 40],
            "眼白": [33, 34],
            "瞳孔": [37, 38],
            "高光": [39, 40],
            "睫毛": [41, 42],
            "眉毛": [45, 46],
            "耳朵": [25, 26],
            "脖子": [5],
            "脖子_身体": [5, 6],
            "衣服": [21, 20],
            "衣服_内层": [20],
            "衣服_外层": [21],
            "配饰": [22],
            "背景": [0],
        }

        def _match(pn: str) -> Tuple[List[int], List[str]]:
            """Return (preferred std IDs, fallback groups) for a part_name."""
            pn_lower = pn.lower().strip()
            # 1. exact direct map
            if pn_lower in direct_map:
                return direct_map[pn_lower], []
            # 2. substring direct map (longest match wins)
            best_ids: List[int] = []
            best_len = 0
            for key, ids in direct_map.items():
                if len(key) > best_len and key in pn_lower:
                    best_ids = ids
                    best_len = len(key)
            if best_ids:
                return best_ids, []
            # 3. keyword → group fallback
            groups: List[str] = []
            for keyword, grps in part_keywords.items():
                if keyword in pn:
                    groups.extend(grps)
            if not groups:
                groups = ["clothes", "body"]
            return [], groups

        for layer in layers_info:
            part_name = layer.get("part_name", "未分类")
            source = layer.get("path", "")
            preferred_ids, fallback_groups = _match(part_name)

            assigned = False
            # Try preferred standard IDs first (in listed order)
            for sid in preferred_ids:
                if sid in used_standard_ids:
                    continue
                for m in mappings:
                    if m.standard_id == sid and not m.mapped:
                        m.mapped = True
                        m.source_file = source
                        m.notes = f"Auto-mapped from part: {part_name}"
                        used_standard_ids.add(sid)
                        assigned = True
                        break
                if assigned:
                    break

            # Fall back to first unmapped layer in target groups
            if not assigned:
                for m in mappings:
                    if (not m.mapped
                            and m.standard_id not in used_standard_ids
                            and m.group in fallback_groups):
                        m.mapped = True
                        m.source_file = source
                        m.notes = f"Auto-mapped (group fallback) from part: {part_name}"
                        used_standard_ids.add(m.standard_id)
                        break

        # Build summary
        required_layers = [m for m in mappings if any(
            l["id"] == m.standard_id and l.get("required")
            for l in self.standard_layers
        )]
        missing_required = [m for m in required_layers if not m.mapped]

        result = {
            "mappings": [asdict(m) for m in mappings],
            "total_layers": len(mappings),
            "mapped_layers": sum(1 for m in mappings if m.mapped),
            "missing_required": [asdict(m) for m in missing_required],
            "physics_config": self.physics_config,
            "parameters": self.standard_params,
        }
        return result

    def generate_config_files(
        self,
        mapping_result: Dict,
        output_dir: str,
        character_name: str = "character",
    ) -> Dict[str, str]:
        """Generate Cubism-compatible config files (JSON guides).

        Returns dict of filename -> filepath for generated files.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        generated = {}

        # 1. Layer mapping guide (JSON)
        mapping_path = out / "layer_mapping.json"
        with open(mapping_path, 'w', encoding='utf-8') as f:
            json.dump(mapping_result, f, ensure_ascii=False, indent=2)
        generated["layer_mapping"] = str(mapping_path)

        # 2. Parameter configuration guide
        params_path = out / "parameters.json"
        params_data = {
            "character": character_name,
            "version": "v9.0",
            "parameters": self.standard_params,
            "notes": "Import these parameters in Cubism Editor after PSD import",
        }
        with open(params_path, 'w', encoding='utf-8') as f:
            json.dump(params_data, f, ensure_ascii=False, indent=2)
        generated["parameters"] = str(params_path)

        # 3. Physics configuration (physics3.json format)
        physics_path = out / "physics3.json"
        with open(physics_path, 'w', encoding='utf-8') as f:
            json.dump(self.physics_config, f, ensure_ascii=False, indent=2)
        generated["physics"] = str(physics_path)

        # 4. Human-readable guide
        guide_path = out / "52_LAYER_GUIDE.txt"
        with open(guide_path, 'w', encoding='utf-8') as f:
            f.write(f"Live2D Master Agent v9.0 - 52-Layer Standard Configuration\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"Character: {character_name}\n")
            f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"Total standard layers: {mapping_result['total_layers']}\n")
            f.write(f"Mapped layers: {mapping_result['mapped_layers']}\n")
            f.write(f"Missing required layers: {len(mapping_result['missing_required'])}\n\n")

            f.write("Layer Structure (back to front):\n")
            f.write("-" * 70 + "\n")
            for m in mapping_result["mappings"]:
                status = "OK" if m["mapped"] else "--"
                req = "*" if any(l["id"] == m["standard_id"] and l.get("required")
                                   for l in self.standard_layers) else " "
                f.write(f" [{status}] {req} [{m['draw_order']:3d}] {m['standard_name_cn']:12s} ({m['standard_name_en']})\n")

            f.write(f"\n* = Required layer\n")
            f.write(f"\nParameters to set up in Cubism Editor: {len(self.standard_params)}\n")
            for p in self.standard_params:
                f.write(f"  - {p['id']}: {p['name']} ({p['min']} to {p['max']}, default={p['default']})\n")

            f.write(f"\nPhysics groups: {len(self.physics_config['physics_settings'])}\n")
            for ps in self.physics_config["physics_settings"]:
                f.write(f"  - {ps['name']} ({len(ps['pendulums'])} pendulum joints)\n")

            f.write("\nImport Workflow:\n")
            f.write("-" * 70 + "\n")
            f.write("1. Import PSD into Cubism Editor\n")
            f.write("2. Verify layers are in correct draw order (using this guide)\n")
            f.write("3. Create ArtMesh for each required layer\n")
            f.write("4. Set up parameters from parameters.json\n")
            f.write("5. Import physics3.json for automatic physics\n")
            f.write("6. Set up blend shapes (expressions)\n")
            f.write("7. Export .moc3 file\n")

        generated["guide"] = str(guide_path)
        log.success(f"Generated {len(generated)} config files in {out}")
        return generated


if __name__ == "__main__":
    gen = Layer52Generator()
    # Test with empty layer info
    result = gen.map_layers_to_standard([])
    print(f"52-layer standard initialized: {result['total_layers']} layers defined")
    print(f"Parameters: {len(gen.standard_params)}")
    print(f"Physics groups: {len(gen.physics_config['physics_settings'])}")
