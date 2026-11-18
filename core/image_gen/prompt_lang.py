"""提示词语言判定与中英风格标签。

中文用户只写中文，但上游模型的语言能力不一样：

- seedream / sensenova 是中文原生模型 —— 中文提示词配中文标签；
- pollinations（SD/Flux）与 OpenAI 兼容端点以英文为主 —— 中文提示词只能追加
  英文标签，并把「语言不匹配」如实告诉调用方，而不是假装没事。

标签追加发生在这里而不是各 provider 里，是为了让「实际发送给模型的字符串」
只有一个来源（``GenerationResult.prompt``），前端可以直接展示。
"""

from dataclasses import dataclass
from typing import Optional
import re

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
        "arms away from body, legs slightly apart, simple white background, "
        "clean line art, cel shading, clear outlines, character design for Live2D rigging"
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

#: 标签分隔符：英文逗号 / 中文逗号 / 顿号 / 分号。
_TAG_SPLIT_RE = re.compile(r"[,，、；;]\s*")


def dedupe_tags(text: str) -> str:
    """按首次出现顺序去重标签（大小写不敏感）。

    中文提示词经词表适配后常与 ``LIVE2D_TAGS`` 撞车：``anime style``、
    ``full body``、``white background`` 会各出现两次。重复不会让画面更好，
    只会抬高这些词的权重，也让用户看不懂「到底发给上游的是什么」。
    """
    seen: set[str] = set()
    kept: list[str] = []
    for raw in _TAG_SPLIT_RE.split(text or ""):
        tag = raw.strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(tag)
    return ", ".join(kept)


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


# ---------------------------------------------------------------------------
# 中文 -> 英文标签的确定性适配（不依赖外部 LLM / 翻译服务）
# ---------------------------------------------------------------------------
#
# Pollinations（SD/Flux）与多数 OpenAI 兼容端点读不懂中文提示词；把中文原样
# 丢过去只会得到"随机写实图"。这里用一张有限词表做贪心替换：
#   * 命中的词 → 英文标签；
#   * 未命中的中文 → 丢弃并统计残留比例；
#   * 纯英文片段 → 原样保留。
# 词表覆盖 web/lib/catalog.ts 的全部标签组与模板词汇，保证界面里点选出来的
# 中文提示词能落到可用的英文标签上。后续新增标签时两侧一起改。
_ZH_EN_TAGS = {
    # 风格
    "日系动漫风格": "anime style",
    "日系动画风格": "anime style",
    "赛璐璐上色": "cel shading",
    "厚涂质感": "painterly rendering",
    "水彩风格": "watercolor style",
    "赛博朋克": "cyberpunk",
    "赛博朋克风格": "cyberpunk",
    "国风插画": "chinese ink illustration",
    # 人物
    "少女": "young girl",
    "少年": "young boy",
    "双马尾": "twin tails",
    "长发": "long hair",
    "短发": "short hair",
    "猫耳": "cat ears",
    "水手服": "sailor uniform",
    "和服": "kimono",
    "异色瞳": "heterochromia",
    "大姐姐": "young woman",
    "机械义肢": "mechanical prosthetic limb",
    # 发型 / 外貌
    "粉色双马尾": "pink twin tails",
    "蓝色眼睛": "blue eyes",
    "银色短发": "silver short hair",
    "栗色长发": "chestnut long hair",
    "长发束冠": "long hair with crown",
    # 服装 / 道具
    "针织毛衣": "knit sweater",
    "青白色长袍": "cyan-white robe",
    "手持折扇": "holding a folding fan",
    "手捧书本": "holding a book",
    # 表情
    "微笑": "smile",
    "害羞": "shy",
    "温柔注视": "gentle gaze",
    "认真": "serious expression",
    "惊讶": "surprised",
    "神情温和": "gentle expression",
    # 构图
    "半身像": "upper body",
    "正面视角": "front view",
    "正面半身构图": "front view, upper body",
    "居中构图": "centered composition",
    "留白背景": "simple background",
    "干净背景": "clean background",
    "全身": "full body",
    "正面朝向": "front facing",
    # 光照 / 配色
    "柔和光影": "soft lighting",
    "逆光": "backlight",
    "边缘光": "rim light",
    "午后阳光": "afternoon sunlight",
    "冷色调边缘光": "cool rim light",
    "柔和色调": "soft color palette",
    "淡雅配色": "elegant color palette",
    "霓虹灯背景": "neon background",
    # 质量
    "高清细节": "high detail",
    "精致五官": "delicate facial features",
    "清晰线条": "clean line art",
    "大师作品": "masterpiece",
    "高清": "high resolution",
    # Live2D 硬约束（与 prompts/image_generation.md 一致）
    "纯白背景": "pure white background",
    "白色背景": "white background",
    "无阴影": "no shadow",
    "无文字": "no text",
    "手臂离开身体": "arms away from body",
    "双腿分开": "legs slightly apart",
}

_SORTED_ZH_TAGS = sorted(_ZH_EN_TAGS.items(), key=lambda item: -len(item[0]))
_CJK_SPLIT = re.compile(r"[，,、。；;：:\s]+")
_NEG_SPLIT = re.compile(r"[，,、；;]+")

#: 只统计"真正需要被翻译的字"：汉字与假名。中文标点（，。；：等）也落在
#: has_cjk 的宽区间里，但它们是分隔符，算进残留比例会让"全部命中词表"的
#: 提示词也误报 prompt_mismatch。
_CJK_WORD_RANGES = (
    (0x3040, 0x30FF),  # 日文假名
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本汉字
    (0xF900, 0xFAFF),  # 兼容汉字
)


def _is_cjk_word(ch: str) -> bool:
    cp = ord(ch)
    return any(start <= cp <= end for start, end in _CJK_WORD_RANGES)


@dataclass(frozen=True)
class PromptAdaptation:
    """一次中文提示词适配的结果。"""

    text: str
    #: 未被词表覆盖的中文字符占原中文的比例（0–1）。
    residual_cjk: float
    #: 是否真的发生了替换（原提示词含中文时为 True）。
    adapted: bool

    @property
    def mismatch(self) -> bool:
        """残留过高说明英文标签不足以表达用户意图，需要前端提示。"""
        return self.residual_cjk > 0.4


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text or "" if _is_cjk_word(ch))


def merge_negative(user: Optional[str], base: str) -> str:
    """合并用户负向词与内置负向词，按逗号去重（大小写不敏感）。

    只在逗号/顿号/分号上切分：负向词本身是英文短语（``bad hands``），
    按空格切会把一个短语拆成两个无意义标签。
    """
    merged: list[str] = []
    seen: set[str] = set()
    for chunk in _NEG_SPLIT.split(f"{user or ''},{base or ''}"):
        tag = chunk.strip(" ，,")
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(tag)
    return ", ".join(merged)


def adapt_prompt(text: str) -> PromptAdaptation:
    """把中文提示词确定性映射成英文标签串。

    纯英文输入原样返回（``adapted=False``），便于调用方区分"翻译过"与"本来
    就是英文"，也避免对英文提示词做无意义的二次加工。
    """
    original = (text or "").strip()
    if not has_cjk(original):
        return PromptAdaptation(original, 0.0, False)

    work = original
    parts: list[str] = []
    for zh, en in _SORTED_ZH_TAGS:
        if zh and zh in work:
            parts.append(en)
            work = work.replace(zh, "，")

    for token in _CJK_SPLIT.split(work):
        token = token.strip(" ，,")
        if token and not has_cjk(token):
            parts.append(token)

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(part.strip())

    original_cjk = max(1, _cjk_count(original))
    residual = _cjk_count(work) / original_cjk
    return PromptAdaptation(", ".join(deduped), residual, True)

