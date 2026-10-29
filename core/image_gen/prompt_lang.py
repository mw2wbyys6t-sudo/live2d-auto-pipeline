"""提示词语言判定与中英风格标签。

中文用户只写中文，但上游模型的语言能力不一样：

- seedream / sensenova 是中文原生模型 —— 中文提示词配中文标签；
- pollinations（SD/Flux）与 OpenAI 兼容端点以英文为主 —— 中文提示词只能追加
  英文标签，并把「语言不匹配」如实告诉调用方，而不是假装没事。

标签追加发生在这里而不是各 provider 里，是为了让「实际发送给模型的字符串」
只有一个来源（``GenerationResult.prompt``），前端可以直接展示。
"""

from typing import Optional

# 常用中日韩区段（含全角标点）。这里只需要区分「用户写的是中文」与「纯英文
# 提示词」，不需要完整的 Unicode Script 判定。
_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK 标点
    (0x3040, 0x30FF),  # 日文假名
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本汉字
    (0xF900, 0xFAFF),  # 兼容汉字
    (0xFF00, 0xFFEF),  # 全角字符
)

LIVE2D_TAGS = {
    "zh": (
        "动漫风格，高质量，杰作，全身，正面朝向，清晰的轮廓线，"
        "简洁的白色背景，适合 Live2D 绑定的角色设计"
    ),
    "en": (
        "anime style, high quality, masterpiece, full body, front-facing, "
        "clear outlines, simple background, character design for Live2D rigging"
    ),
}

LIVE2D_NEGATIVE_TAGS = {
    "zh": (
        "低分辨率，画面模糊，肢体残缺，多余的手指，文字，签名，水印，"
        "背景杂乱，写实风格"
    ),
    "en": (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, "
        "extra digit, fewer digits, cropped, worst quality, low quality, "
        "jpeg artifacts, signature, watermark, username, blurry, deformed, "
        "mutated, ugly, disfigured, 3d, realistic, photorealistic, nsfw"
    ),
}

_SEPARATOR = {"zh": "，", "en": ", "}


def has_cjk(text: str) -> bool:
    for ch in text or "":
        cp = ord(ch)
        for start, end in _CJK_RANGES:
            if start <= cp <= end:
                return True
    return False


def detect_lang(text: str) -> str:
    """返回 ``zh`` 或 ``en``：只要出现一个中文字符就按中文处理。"""
    return "zh" if has_cjk(text) else "en"


def live2d_tags(lang: str) -> str:
    return LIVE2D_TAGS.get(lang, LIVE2D_TAGS["en"])


def negative_tags(lang: str) -> str:
    return LIVE2D_NEGATIVE_TAGS.get(lang, LIVE2D_NEGATIVE_TAGS["en"])


def append_live2d_tags(prompt: str, lang: Optional[str] = None) -> str:
    """把风格标签接到用户提示词后面；语言缺省时按提示词本身判定。"""
    lang = lang or detect_lang(prompt)
    tags = live2d_tags(lang)
    if not (prompt or "").strip():
        return tags
    sep = _SEPARATOR.get(lang, ", ")
    return f"{prompt}{sep}{tags}"
