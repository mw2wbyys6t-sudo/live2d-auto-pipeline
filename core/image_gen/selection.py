"""多候选择优：用确定性评分从同一提示词的多张生成图里挑最像"可绑定立绘"的一张。

评分刻意保持轻量、无新依赖、可单测：

* 50% 质量分：复用 ``core/qa/engine.py`` 的既有评分；
* 30% 剪影分：前景覆盖率落在 10%–55% 之间最理想（太小=空镜，太大=贴边）；
* 20% 居中度：前景 bbox 中心离画布中心越近越高。

不引入人脸检测：core 里没有现成的 YuNet 封装，硬加会把"测试连接"与生成
都拖上一个额外模型依赖。剪影 + 质量分已经足以排除"糊、空、贴边"的候选。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from core.utils.image_utils import estimate_foreground_silhouette


def silhouette_score(mask: np.ndarray) -> float:
    """前景覆盖率的线性打分：0.10–0.55 满分，向两侧线性衰减。"""
    if mask is None or mask.size == 0:
        return 0.0
    coverage = float(np.asarray(mask, dtype=bool).mean())
    if 0.10 <= coverage <= 0.55:
        return 1.0
    if coverage <= 0.0:
        return 0.0
    if coverage < 0.10:
        return max(0.0, coverage / 0.10)
    return max(0.0, 1.0 - (coverage - 0.55) / 0.45)


def centering_score(mask: np.ndarray) -> float:
    """前景 bbox 中心与画布中心的偏移打分（0=贴边/偏心，1=完全居中）。"""
    if mask is None or mask.size == 0:
        return 0.0
    mask = np.asarray(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return 0.0
    height, width = mask.shape[:2]
    cx = (float(xs.min()) + float(xs.max())) / 2.0 / max(1, width)
    cy = (float(ys.min()) + float(ys.max())) / 2.0 / max(1, height)
    offset = abs(cx - 0.5) * 2.0 + abs(cy - 0.5) * 2.0
    return max(0.0, 1.0 - offset)


def _foreground_mask(rgba: Image.Image) -> np.ndarray:
    alpha = np.array(rgba.getchannel("A"))
    if float((alpha > 8).mean()) >= 0.02:
        return alpha > 8
    return estimate_foreground_silhouette(np.array(rgba.convert("RGB")))


def _quality_score(rgba: Image.Image) -> float:
    """QA 分归一化到 0–1；QA 不可用时退回中性 0.5。"""
    try:
        from core.qa.engine import QAEngine

        report = QAEngine().assess_image(rgba)
        return max(0.0, min(1.0, float(report.score) / 100.0))
    except Exception:
        return 0.5


def score_candidate(img: Image.Image) -> float:
    """返回 0–1 的候选质量分（越高越适合作为立绘）。"""
    rgba = img.convert("RGBA")
    mask = _foreground_mask(rgba)
    return (
        0.5 * _quality_score(rgba)
        + 0.3 * silhouette_score(mask)
        + 0.2 * centering_score(mask)
    )
