"""本地兜底占位图的"如实标注"回归测试。

实测（2026-10-30，已安装的 v0.10.10）：占位图水印要把用户的中文提示词画上去，
但 Pillow 默认字体没有 CJK 字形，画出来是一排豆腐块 —— 这块水印存在的唯一
理由就是"绝不冒充 AI 生成结果"，中文下它反而成了最难读的一块。

这里只测**规则**（有字体才写中文），不测渲染像素：豆腐块要真渲染才看得见，
而规则本身与 Pillow 无关。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lig():
    spec = importlib.util.spec_from_file_location(
        "local_image_generator", _ROOT / "local_image_generator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cjk_font_is_found_on_this_machine(lig):
    """候选表里至少要有一个是真的，否则这条路径在这台机器上就是哑的。

    随包 Python 不捆字体，靠的是系统字体。Windows / 常见 Linux 桌面都有；
    真的找不到时下面几条规则保证我们退回 ASCII，而不是画豆腐块。
    """
    font = lig._label_font()
    assert font is not None, "本机没找到任何中文字体候选，检查 _CJK_FONT_CANDIDATES"


def test_prompt_is_written_when_font_available(lig):
    label = lig._placeholder_label(42, "银发少女，赛璐璐画风", has_cjk_font=True)

    assert "LOCAL-PLACEHOLDER" in label
    assert "银发少女，赛璐璐画风" in label


def test_cjk_prompt_dropped_instead_of_rendering_tofu(lig):
    label = lig._placeholder_label(42, "银发少女，赛璐璐画风", has_cjk_font=False)

    assert "银发" not in label, "没有中文字体时不得把中文写进水印（画出来是豆腐块）"
    assert "LOCAL-PLACEHOLDER" in label, "占位标记本身必须始终在"


def test_ascii_prompt_survives_without_font(lig):
    label = lig._placeholder_label(42, "silver hair girl", has_cjk_font=False)

    assert "silver hair girl" in label


def test_empty_prompt_still_marks_placeholder(lig):
    for has_font in (True, False):
        label = lig._placeholder_label(7, "   ", has_cjk_font=has_font)
        assert label.startswith("LOCAL-PLACEHOLDER")
        assert "seed=7" in label


def test_newlines_cannot_break_the_single_line_watermark(lig):
    label = lig._placeholder_label(1, "少女\n赛璐璐", has_cjk_font=True)

    assert "\n" not in label
