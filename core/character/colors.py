"""把 hex 色值翻译成模型读得懂的颜色名（中文 / 英文）。

历史问题：角色一致性后缀直接把 ``#3a2a1a`` 拼进提示词（"#3a2a1a hair"）。
图像模型不认识 hex，这个「一致性锚点」实际上等于没写，用户却以为它在生效。
"""

import re
from typing import Optional, Tuple

# (中文名, 英文名, RGB)。同一行两语言指向同一个色点，避免两套词表漂移。
_PALETTE = (
    ("黑色", "black", (16, 16, 16)),
    ("深灰", "dark gray", (70, 70, 70)),
    ("灰色", "gray", (130, 130, 130)),
    ("白色", "white", (248, 248, 248)),
    ("深棕", "dark brown", (58, 42, 26)),
    ("棕色", "brown", (120, 72, 40)),
    ("栗色", "auburn", (150, 80, 50)),
    ("浅棕", "light brown", (139, 111, 78)),
    ("金色", "blonde", (232, 196, 120)),
    ("浅金", "pale blonde", (247, 228, 170)),
    ("橙色", "orange", (235, 140, 50)),
    ("红色", "red", (220, 60, 55)),
    ("酒红", "wine red", (140, 30, 50)),
    ("粉色", "pink", (240, 150, 180)),
    ("紫色", "purple", (150, 90, 200)),
    ("深蓝", "navy blue", (30, 50, 120)),
    ("蓝色", "blue", (70, 110, 210)),
    ("天蓝", "sky blue", (95, 180, 230)),
    ("青色", "cyan", (60, 200, 200)),
    ("绿色", "green", (80, 170, 90)),
    ("黄绿", "yellow green", (170, 200, 80)),
    ("黄色", "yellow", (240, 215, 70)),
    ("浅肤", "fair", (245, 213, 184)),
    ("橄榄", "olive", (110, 100, 60)),
)

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def parse_hex(value: str) -> Optional[Tuple[int, int, int]]:
    """解析 ``#rgb`` / ``#rrggbb``；不是颜色值就返回 None。"""
    if not isinstance(value, str):
        return None
    match = _HEX_RE.match(value.strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _srgb_to_lab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    def linear(channel: int) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def pivot(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = pivot(x), pivot(y), pivot(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def describe_color(value: str, lang: str = "en") -> str:
    """返回最接近的自然语言颜色名；非 hex 输入原样返回。"""
    rgb = parse_hex(value)
    if rgb is None:
        return value
    target = _srgb_to_lab(rgb)
    best = None
    best_distance = float("inf")
    for zh_name, en_name, palette_rgb in _PALETTE:
        lab = _srgb_to_lab(palette_rgb)
        distance = sum((a - b) ** 2 for a, b in zip(target, lab))
        if distance < best_distance:
            best_distance = distance
            best = (zh_name, en_name)
    assert best is not None
    return best[0] if lang == "zh" else best[1]
