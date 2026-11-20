"""手臂/手掌提示词的召回回归。

实测（2026-11-20，1024×1536 立绘，sam2_groundingdino）：
``arm . sleeve`` 只召回持麦那只手；另一侧张开的五指手掌完全没被框出来，
像素最终被并进 ``clothes_top``/``waistband``，manifest 只剩 ``arm:R`` 缺件，
模型里也生不出任何手臂参数。加上 ``open hand . palm . fingers`` 后同一张图
直接框出了手掌（box 368,288-427,363）。

这两条断言很浅，但锁的是「别把召回词删回去」——删掉不会有任何测试变红，
只会在成品里表现为「手不见了」。
"""
from __future__ import annotations

from core.segment_engine.sam2_gd import PART_PROMPTS


def test_hands_prompt_asks_for_open_palm_and_fingers() -> None:
    phrase = PART_PROMPTS["hands"]
    for term in ("open hand", "palm", "fingers"):
        assert term in phrase, f"hands 提示词缺少 {term!r}：{phrase}"


def test_arms_prompt_asks_for_bare_arm() -> None:
    phrase = PART_PROMPTS["arms"]
    assert "bare arm" in phrase, f"arms 提示词缺少裸露手臂写法：{phrase}"
    # 保留原有写法，避免只覆盖袖子导致皮肤部分丢失
    assert "sleeve" in phrase
