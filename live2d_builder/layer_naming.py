"""层名分类：把不同分层器的命名归一到部件类别。

同一份立绘可能来自四种分层器，层名写法完全不同：

* K-means：``头发_后`` / ``衣服``
* SAM2/ISNet：``hair_back`` / ``clothes_top``
* l2d_sam_iter2：``hair`` / ``bottomwear``
* PSD 导入：``000_hair_back`` / ``Hair_Back``

变形器 targets 不能靠穷举层名，否则换一个分层器或换一个角色就整条失配
（历史问题：``arm_L`` 与 ``arm_l`` 大小写不一致导致肩部变形器 0 个）。
这里提供统一的整词匹配，供变形器、物理构建器与导出器共用。
"""

from __future__ import annotations

import re
from typing import Iterable, Set

#: 头发（前后不分）。
HAIR_KEYWORDS = ("hair", "头发", "毛发")

#: 后发：比 ``hair`` 更具体，用于从头发里再分出后发摆链。
HAIR_BACK_KEYWORDS = (
    "hair_back", "hairback", "back_hair", "hair_shadow_back",
    "后发", "头发_后",
)

#: 下装（裙 / 裤）。
SKIRT_KEYWORDS = (
    "skirt", "bottomwear", "clothes_bottom", "skirt_body",
    "裙", "裤",
)

#: 尾巴。只认 ``tail`` 整词 —— ``detail`` 里也含 tail，按子串会误判。
TAIL_KEYWORDS = ("tail", "尾巴", "尾")

#: 兽耳。必须比 ``ear`` 更具体：19 类的 ``earwear``（耳饰）与人类的
#: ``ear_L`` 都不能被当成会摆动的兽耳。
ANIMAL_EAR_KEYWORDS = (
    "animal_ear", "animal_ears", "beast_ear", "beast_ears",
    "cat_ear", "cat_ears", "fox_ear", "fox_ears",
    "wolf_ear", "wolf_ears", "kemomimi",
    "兽耳", "猫耳", "狐耳", "狼耳",
)

_TOKEN_SPLIT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
#: 驼峰边界：``HairFront`` / ``hairBack`` 也要能拆成 hair + front/back，
#: 否则 PSD 与部分后端输出的驼峰名一个关键词都命中不了。
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def layer_tokens(name: str) -> Set[str]:
    """把层名切成小写词元：``000_Hair-Back`` -> ``{"000","hair","back"}``。"""
    text = _CAMEL_BOUNDARY.sub("_", str(name or ""))
    return {token for token in _TOKEN_SPLIT.split(text.lower()) if token}


def matches_keywords(name: str, keywords: Iterable[str]) -> bool:
    """层名是否命中任一关键词。

    匹配规则分两种，是为了兼顾误判与漏判：

    * 单个 ASCII 词（``tail`` / ``hair`` / ``bottomwear``）按**整词**匹配，
      ``tail`` 因此不会命中 ``detail``；
    * 含分隔符的词或中文（``hair_back`` / ``头发_后`` / ``裙``）按**子串**匹配，
      因为中文层名没有分隔符，``头发_后`` 必须能被 ``头发`` 命中。

    Args:
        name: 原始层名（可带序号前缀、大小写混用）。
        keywords: 关键词表，见本模块顶部的常量。

    Returns:
        命中任一关键词返回 ``True``。
    """
    text = str(name or "")
    # 驼峰先拆成下划线（HairBack -> hair_back），否则复合关键词在驼峰名上
    # 一个都命中不了；中文不受这个替换影响。
    lowered = _CAMEL_BOUNDARY.sub("_", text).lower()
    tokens = layer_tokens(text)
    for raw in keywords:
        keyword = str(raw or "").lower()
        if not keyword:
            continue
        if keyword.isascii() and "_" not in keyword and "-" not in keyword:
            if keyword in tokens:
                return True
        elif keyword in lowered:
            return True
    return False
