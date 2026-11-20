"""中文提示词 → 英文标签的确定性适配（本轮质量改造的回归闸门）。

不依赖外部翻译服务：词表覆盖 web/lib/catalog.ts 的标签与模板词汇；
未覆盖的中文会被丢弃并计入 residual，用于如实提示"没表达出来"。
"""

from core.image_gen.prompt_lang import adapt_prompt, dedupe_tags, merge_negative


class TestDedupeTags:
    """适配后的英文标签会与 LIVE2D_TAGS 撞车，重复不该被二次加权。"""

    def test_case_insensitive_dedupe_keeps_first(self):
        assert dedupe_tags("Anime style, full body, anime style, white background") == (
            "Anime style, full body, white background"
        )

    def test_handles_chinese_separators(self):
        assert dedupe_tags("粉色双马尾，蓝眼睛、粉色双马尾") == "粉色双马尾, 蓝眼睛"

    def test_empty_input(self):
        assert dedupe_tags("") == ""
        assert dedupe_tags(None) == ""  # type: ignore[arg-type]


class TestAdaptPrompt:
    def test_known_chinese_tags_become_english(self):
        result = adapt_prompt("粉色双马尾，蓝色眼睛，水手服，微笑")
        assert result.adapted is True
        assert result.residual_cjk == 0
        assert result.mismatch is False
        for tag in ("pink twin tails", "blue eyes", "sailor uniform", "smile"):
            assert tag in result.text, result.text

    def test_longest_match_wins(self):
        # 「日系动漫风格」必须整体命中，不能被「日系」之类的短词拆开。
        result = adapt_prompt("日系动漫风格")
        assert "anime style" in result.text
        assert "动漫风格" not in result.text

    def test_english_prompt_untouched(self):
        result = adapt_prompt("pink twintails, blue eyes")
        assert result.text == "pink twintails, blue eyes"
        assert result.adapted is False
        assert result.residual_cjk == 0

    def test_unknown_chinese_marks_mismatch(self):
        result = adapt_prompt("紫色蝴蝶结搭配蕾丝花边")
        assert result.mismatch is True
        assert result.residual_cjk == 1.0

    def test_partial_coverage(self):
        result = adapt_prompt("少女，紫色蝴蝶结")
        assert "young girl" in result.text
        assert 0 < result.residual_cjk < 1
        assert result.mismatch is True

    def test_live2d_hard_constraints(self):
        result = adapt_prompt("全身，正面视角，纯白背景，无文字")
        for tag in ("full body", "front view", "pure white background", "no text"):
            assert tag in result.text, result.text


class TestMergeNegative:
    def test_dedupes_and_keeps_order(self):
        merged = merge_negative("lowres, blurry", "lowres, bad hands")
        assert merged == "lowres, blurry, bad hands"

    def test_empty_user_still_has_base(self):
        assert "bad anatomy" in merge_negative(None, "bad anatomy")

    def test_chinese_user_negative_survives(self):
        merged = merge_negative("多余手指，文字", "lowres")
        assert "多余手指" in merged and "lowres" in merged
