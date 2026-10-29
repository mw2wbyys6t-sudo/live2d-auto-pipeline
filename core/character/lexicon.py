"""角色卡枚举字段的中英词表。

未收录的值原样返回 —— 宁可保留用户/上游写入的原值，也不猜一个翻译。
"""

from typing import Dict

_TERMS: Dict[str, Dict[str, Dict[str, str]]] = {
    "face_shape": {
        "oval": {"zh": "鹅蛋", "en": "oval"},
        "round": {"zh": "圆", "en": "round"},
        "slim": {"zh": "瘦", "en": "slim"},
    },
    "hair_style": {
        "long": {"zh": "长发", "en": "long hair"},
        "short": {"zh": "短发", "en": "short hair"},
        "twintail": {"zh": "双马尾", "en": "twintails"},
        "ponytail": {"zh": "马尾", "en": "ponytail"},
        "bob": {"zh": "波波头", "en": "bob cut"},
    },
    "eye_shape": {
        "large_anime": {"zh": "大眼睛", "en": "large anime eyes"},
        "almond": {"zh": "杏眼", "en": "almond eyes"},
        "round": {"zh": "圆眼", "en": "round eyes"},
        "narrow": {"zh": "细长眼", "en": "narrow eyes"},
    },
    "eyebrow_shape": {
        "soft_arch": {"zh": "柔和弯眉", "en": "soft arched eyebrows"},
        "straight": {"zh": "平眉", "en": "straight eyebrows"},
        "thick": {"zh": "粗眉", "en": "thick eyebrows"},
    },
    "bangs_style": {
        "straight": {"zh": "齐刘海", "en": "straight bangs"},
        "side": {"zh": "侧分刘海", "en": "side-swept bangs"},
        "parted": {"zh": "中分刘海", "en": "center-parted bangs"},
        "none": {"zh": "无刘海", "en": "no bangs"},
    },
    "body_type": {
        "slim": {"zh": "纤细", "en": "slim"},
        "average": {"zh": "标准", "en": "average"},
        "curvy": {"zh": "丰满", "en": "curvy"},
    },
    "proportions": {
        "anime_standard": {"zh": "动漫标准", "en": "anime standard"},
    },
}


def term(field: str, value: str, lang: str = "en") -> str:
    """返回字段值在目标语言下的说法；未收录时原样返回。"""
    entry = _TERMS.get(field, {}).get(value)
    if not entry:
        return value
    return entry.get(lang, entry["en"])
