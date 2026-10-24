"""训练用的标签体系。

刻意不 import torch，让标注工具和测试在无 torch 环境下也能复用。

9 类 = 背景 + 8 个核心部位。这 8 个正对应
``core.segment_engine.layer_quality.REQUIRED_PARTS``，也就是说模型只要把这
8 类分对，就能通过管线现成的分层质量门禁。想扩到 35 个 Live2D 细部位时，
在这里追加即可，其余代码按 ``NUM_CLASSES`` 自适应。
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

BACKGROUND = "background"

CORE_PARTS: Tuple[str, ...] = (
    "hair_back",
    "hair_front",
    "face",
    "eyes_left",
    "eyes_right",
    "mouth",
    "neck",
    "clothes_top",
)

LABELS: Tuple[str, ...] = (BACKGROUND,) + CORE_PARTS
LABEL_TO_INDEX: Dict[str, int] = {name: index for index, name in enumerate(LABELS)}
INDEX_TO_LABEL: Dict[int, str] = {index: name for name, index in LABEL_TO_INDEX.items()}
NUM_CLASSES: int = len(LABELS)

CLASS_COLORS: Dict[str, Tuple[int, int, int]] = {
    BACKGROUND: (0, 0, 0),
    "hair_back": (128, 0, 128),
    "hair_front": (255, 0, 255),
    "face": (255, 220, 180),
    "eyes_left": (0, 255, 255),
    "eyes_right": (0, 128, 255),
    "mouth": (255, 0, 128),
    "neck": (200, 160, 120),
    "clothes_top": (180, 0, 255),
}

_PALETTE: np.ndarray = np.array([CLASS_COLORS[name] for name in LABELS], dtype=np.uint8)
_RGB_TO_INDEX: Dict[Tuple[int, int, int], int] = {
    tuple(int(v) for v in color): index for index, color in enumerate(_PALETTE)
}


def label_map_to_rgb(label_map: np.ndarray) -> np.ndarray:
    """把类别索引图转成可视化的 RGB 图。"""
    indices = np.clip(np.asarray(label_map, dtype=np.int64), 0, NUM_CLASSES - 1)
    return _PALETTE[indices]


def rgb_to_label_map(rgb: np.ndarray) -> np.ndarray:
    """按调色板精确匹配回类别索引；未命中的颜色归为背景。"""
    array = np.asarray(rgb, dtype=np.uint8)
    if array.ndim == 3 and array.shape[2] == 4:
        array = array[..., :3]
    result = np.zeros(array.shape[:2], dtype=np.uint8)
    for (red, green, blue), index in _RGB_TO_INDEX.items():
        hit = (array[..., 0] == red) & (array[..., 1] == green) & (array[..., 2] == blue)
        result[hit] = index
    return result


def color_table() -> str:
    """人类可读的类别表，贴到标注工具文档里用。"""
    lines = [f"{index:>2}  {name:<14} RGB{CLASS_COLORS[name]}" for index, name in enumerate(LABELS)]
    return "\n".join(lines)
