"""角色一致性提示词的语言与可读性。

两件事必须同时成立：
1. 中文用户的提示词后缀是中文（不塞英文枚举）；
2. 色值转成颜色名（``dark brown`` / ``深棕``），不再把 ``#3a2a1a`` 直接发给模型。
"""

from core.character.card import CharacterCard
from core.character.colors import describe_color, parse_hex


class TestDescribeColor:
    def test_default_palette_maps_to_names(self):
        assert describe_color("#3a2a1a", "zh") == "深棕"
        assert describe_color("#3a2a1a", "en") == "dark brown"
        assert describe_color("#f5d5b8", "zh") == "浅肤"
        assert describe_color("#f5d5b8", "en") == "fair"
        assert describe_color("#8b6f4e", "zh") == "浅棕"

    def test_primary_colors_map_without_hash(self):
        assert describe_color("#0066FF", "en") == "blue"
        assert describe_color("#0066ff", "zh") == "蓝色"

    def test_non_hex_values_pass_through(self):
        assert describe_color("blue", "zh") == "blue"
        assert describe_color("#zzz", "zh") == "#zzz"
        assert describe_color("", "zh") == ""

    def test_short_hex_supported(self):
        assert parse_hex("#abc") == (170, 187, 204)


class TestStylePromptLanguage:
    def make_card(self) -> CharacterCard:
        return CharacterCard(
            name="小雪",
            hair_color="#3a2a1a",
            eye_color="#4a90d9",
            hair_style="twintail",
            face_shape="oval",
            skin_tone="#f5d5b8",
        )

    def test_chinese_prompt_has_no_hex_or_english_enums(self):
        prompt = self.make_card().generate_style_prompt(lang="zh")
        assert "#" not in prompt
        assert "twintail" not in prompt
        assert "双马尾" in prompt
        assert "深棕" in prompt
        assert "小雪" in prompt

    def test_english_prompt_uses_color_names_instead_of_hex(self):
        prompt = self.make_card().generate_style_prompt()
        assert "#" not in prompt
        assert "dark brown" in prompt
        assert "twintail" in prompt

    def test_default_card_has_no_hex(self):
        # 历史缺陷：默认卡片直接产出 "#3a2a1a hair"。
        assert "#" not in CharacterCard().generate_style_prompt()

    def test_unknown_enum_values_are_kept_verbatim(self):
        card = CharacterCard(hair_style="space-buns")
        zh = card.generate_style_prompt(lang="zh")
        en = card.generate_style_prompt(lang="en")
        assert "space-buns" in zh and "space-buns" in en

    def test_chinese_user_text_is_preserved(self):
        card = CharacterCard(name="阿岚", style_constraints="赛博朋克，霓虹灯光")
        prompt = card.generate_style_prompt(lang="zh")
        assert "赛博朋克，霓虹灯光" in prompt


class TestNegativePromptLanguage:
    def test_chinese_negative_is_chinese(self):
        neg = CharacterCard().generate_negative_prompt(lang="zh")
        assert "低质量" in neg and "模糊" in neg
        assert "low quality" not in neg

    def test_english_negative_stays_english(self):
        neg = CharacterCard().generate_negative_prompt()
        assert "low quality" in neg
        assert "低质量" not in neg

    def test_user_negative_is_kept_in_both_languages(self):
        card = CharacterCard(negative_prompt="多余的耳朵")
        assert "多余的耳朵" in card.generate_negative_prompt(lang="zh")
        assert "多余的耳朵" in card.generate_negative_prompt(lang="en")
