#!/usr/bin/env python3
"""Live2D Master Agent - PSD 图层名 → 语义部件识别.

借鉴 psd2live（tsunehimatoi/psd2live，GPL-3.0）的"按图层名识别部件"思路：
导入的 PSD 通常带有语义化图层名（英文/中文/日文），识别出部件类别与左右
侧向后，下游的自动绑定（参数 → 部件映射）才有稳定的目标。

注意：本模块只借鉴思路与命名约定，全部代码为原创（psd2live 为 GPL-3.0，
与本仓库 Apache-2.0 不兼容，禁止复制其代码）。
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# 支持的部件类别（与 moc3 参数绑定使用的部件名保持一致）
_PARTS = (
    "eye_white", "eye_iris", "eye_pupil", "eyelash", "eyebrow",
    "mouth", "nose", "face", "ear",
    "hair_front", "hair_side", "hair_back", "ahoge",
    "body", "clothes", "arm", "hand", "leg",
    "accessory", "blush", "highlight", "background",
)

# 部件关键词表（小写）；含英/中/日常见写法
_PART_KEYWORDS: Dict[str, tuple] = {
    "eye_white": ("eye_white", "eyewhite", "sclera", "白眼", "眼白", "白目"),
    "eye_iris": ("iris", "eye_iris", "eye", "虹膜", "瞳孔彩", "眼球", "瞳", "目"),
    "eye_pupil": ("pupil", "瞳孔", "瞳芯"),
    "eyelash": ("lash", "eyelash", "睫毛", "まつげ", "まつ毛"),
    "eyebrow": ("brow", "eyebrow", "眉毛", "まゆ", "眉"),
    "mouth": ("mouth", "lip", "嘴", "唇", "口"),
    "nose": ("nose", "鼻", "はな"),
    "face": ("face", "cheek", "脸", "臉", "颜", "顔", "かお", "面部"),
    "ear": ("ear", "耳", "みみ"),
    "hair_front": ("hair_front", "front_hair", "bangs", "fringe", "前发", "前髪", "前髮", "刘海", "瀏海", "まえがみ"),
    "hair_side": ("hair_side", "side_hair", "sidelock", "侧发", "側發", "側髪", "鬓", "鬢", "もみあげ"),
    "hair_back": ("hair_back", "back_hair", "后发", "後髪", "后髪", "うしろがみ", "後ろ髪"),
    "ahoge": ("ahoge", "呆毛", "アホ毛"),
    "body": ("body", "torso", "neck", "身体", "胴体", "脖子", "颈", "頸", "からだ", "身体姿勢"),
    "clothes": ("cloth", "clothes", "clothing", "dress", "shirt", "skirt", "衣", "服", "裙", "服裝"),
    "arm": ("arm", "shoulder", "手臂", "胳膊", "肩", "腕", "うで"),
    "hand": ("hand", "palm", "手指", "手"),
    "leg": ("leg", "thigh", "腿", "大腿", "脚", "足", "あし"),
    "accessory": ("accessory", "ribbon", "bow", "hat", "glasses", "ornament", "饰", "飾", "饰品", "蝴蝶结", "帽子", "眼镜", "リボン", "かざり"),
    "blush": ("blush", "脸红", "腮红", "頬"),
    "highlight": ("highlight", "shine", "高光", "ハイライト"),
    "background": ("background", "bg", "backdrop", "背景", "はいけい"),
}

# 左右侧向关键词
_SIDE_KEYWORDS = {
    "left": ("left", "_l_", "_l", " l", "左侧", "左", "left ", "hidari", "左目", "左腕"),
    "right": ("right", "_r_", "_r", " r", "右侧", "右", "right ", "migi", "右目", "右腕"),
}

# 口型开合素材（a/i/u/e/o 口型约定，含中文写法）
_MOUTH_VOWELS = ("a", "i", "u", "e", "o")

_WORD_SPLIT = re.compile(r"[\s_\-\.()\[\]（）\[\]【】·・/\\]+")


def _candidate_parts(tokens: list) -> list:
    """返回 (score, keyword_len, part, keyword) 候选列表，按具体度降序。

    多词关键词（如 eye_white）按"全部词元都出现"匹配，得分 = 词元数；
    单词关键词按子串/词元匹配，得分 = 1。更具体的（词元多、关键词长）
    优先，避免 "eye" 抢走 "eye_white"。
    """
    candidates = []
    joined = "_".join(tokens)
    lowered = " ".join(tokens)
    for part, keywords in _PART_KEYWORDS.items():
        for kw in keywords:
            kw_tokens = [t for t in _WORD_SPLIT.split(kw.lower()) if t]
            if len(kw_tokens) > 1:
                if all(kt in tokens for kt in kw_tokens):
                    candidates.append((len(kw_tokens), len(kw), part, kw))
            elif kw in lowered or kw in joined:
                candidates.append((1, len(kw), part, kw))
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates


def recognize_part(layer_name: str) -> Dict[str, Optional[str]]:
    """从图层名识别语义部件。

    Returns:
        {"part": <部件类别或 None>, "side": "left"|"right"|None,
         "mouth_vowel": "a"|"i"|"u"|"e"|"o"|None, "matched_by": <命中的关键词>}
    """
    name = (layer_name or "").strip()
    lowered = name.lower()
    tokens = [t for t in _WORD_SPLIT.split(lowered) if t]

    result: Dict[str, Optional[str]] = {
        "part": None, "side": None, "mouth_vowel": None, "matched_by": None,
    }
    if not name:
        return result

    # 1) 部件识别：最具体的关键词优先
    candidates = _candidate_parts(tokens)
    if candidates:
        _, _, part, kw = candidates[0]
        result["part"] = part
        result["matched_by"] = kw

    # 2) 左右配对：优先识别中文/英文显式左右，避免把 "left" 误配给含 "l" 的词
    #    （先长词后短词）
    for side in ("left", "right"):
        for kw in _SIDE_KEYWORDS[side]:
            if kw in lowered:
                result["side"] = side
                break
        if result["side"]:
            break

    # 3) 口型元音（如 mouth_a / 嘴_a / 口-i）
    if result["part"] == "mouth":
        for v in _MOUTH_VOWELS:
            if re.search(rf"(?<![a-z]){v}(?![a-z])", lowered):
                result["mouth_vowel"] = v
                break

    return result


__all__ = ["recognize_part"]
