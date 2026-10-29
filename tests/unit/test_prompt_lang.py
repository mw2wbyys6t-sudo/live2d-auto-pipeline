"""提示词语言判定与「实际发送给模型」的记录。

取证背景（v0.10.10）：中文提示词会被无条件追加英文标签，用户看不到真正
发出去的字符串，出了问题只能猜。这里钉住两件事：
1. 中文提示词 → 中文标签（中文原生模型），英文提示词 → 英文标签；
2. ``GenerationResult.prompt`` 必须是真正发给上游的那串文字。
"""

import base64
from types import SimpleNamespace

import pytest

from core.image_gen import openai_compat
from core.image_gen.prompt_lang import (
    append_live2d_tags,
    detect_lang,
    has_cjk,
    live2d_tags,
    negative_tags,
)


class TestHasCjk:
    def test_chinese(self):
        assert has_cjk("粉色双马尾")

    def test_english(self):
        assert not has_cjk("pink twintails, blue eyes")

    def test_mixed(self):
        assert has_cjk("pink 双马尾")

    def test_fullwidth_punctuation(self):
        assert has_cjk("ｐｉｎｋ？")

    def test_empty(self):
        assert not has_cjk("")
        assert not has_cjk(None)  # type: ignore[arg-type]


class TestTags:
    def test_chinese_prompt_gets_chinese_tags(self):
        out = append_live2d_tags("正面全身立绘，粉色双马尾")
        assert out.startswith("正面全身立绘，粉色双马尾")
        assert "杰作" in out
        assert "masterpiece" not in out
        assert "，" in out

    def test_english_prompt_gets_english_tags(self):
        out = append_live2d_tags("pink twintails, front view")
        assert out.startswith("pink twintails, front view")
        assert "masterpiece" in out
        assert "杰作" not in out

    def test_empty_prompt_returns_tags_only(self):
        assert append_live2d_tags("   ") == live2d_tags("en")
        assert append_live2d_tags("", "zh") == live2d_tags("zh")

    def test_prompt_language_wins_by_default(self):
        assert detect_lang("画一只猫") == "zh"
        assert detect_lang("a cat") == "en"

    def test_chinese_and_english_negatives_differ(self):
        assert "低分辨率" in negative_tags("zh")
        assert "lowres" in negative_tags("en")


class TestProviderRecordsSentPrompt:
    """provider 的 ``prompt`` 字段必须含标签，不能只记用户原文。"""

    def test_seedream_builds_chinese_tags_for_chinese_prompt(self):
        from core.image_gen.seedream import SeedreamProvider

        provider = SeedreamProvider.__new__(SeedreamProvider)
        sent = provider._build_prompt("正面全身立绘")
        assert "杰作" in sent and "masterpiece" not in sent

    def test_sensenova_builds_english_tags_for_english_prompt(self):
        from core.image_gen.sensenova import SenseNovaProvider

        provider = SenseNovaProvider.__new__(SenseNovaProvider)
        sent = provider._optimize_prompt("pink twintails")
        assert "masterpiece" in sent and "杰作" not in sent

    def test_pollinations_negative_follows_prompt_language(self):
        from core.image_gen.pollinations import PollinationsProvider

        provider = PollinationsProvider.__new__(PollinationsProvider)
        sent = provider._build_live2d_prompt("粉色双马尾")
        assert "masterpiece" in sent  # SD/Flux 系只认英文标签
        assert provider._prompt_metadata("粉色双马尾")["prompt_lang"] == "zh"
        assert provider._prompt_metadata("粉色双马尾")["prompt_mismatch"] is True
        assert provider._prompt_metadata("pink twintails")["prompt_mismatch"] is False


class TestOpenAICompatSuccessPath:
    """回归：成功路径曾用未定义的 ``success``/``seed`` 参数构造 GenerationResult，
    一旦端点真的返回图片就会抛 TypeError。"""

    def test_success_returns_result_with_sent_prompt(self, monkeypatch, tmp_path):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"data": [{"b64_json": base64.b64encode(png).decode()}]}

        monkeypatch.setattr(
            openai_compat._CheckedURL,
            "parse",
            staticmethod(lambda url, hosts=None: SimpleNamespace(url=url)),
        )
        monkeypatch.setattr(openai_compat.requests, "post", lambda *a, **k: FakeResponse())

        cfg = SimpleNamespace(
            openai_api_key="test-key",
            openai_base_url="https://gateway.example.com/v1",
            image_model="test-model",
            output_dir=str(tmp_path),
        )
        provider = openai_compat.OpenAICompatProvider(cfg)
        result = provider.generate(
            "粉色双马尾",
            output_path=str(tmp_path / "out.png"),
            negative_prompt="低质量",
            seed=42,
        )

        assert result.success is True
        assert result.prompt == "粉色双马尾\n\nAvoid: 低质量"
        assert result.metadata["prompt_lang"] == "zh"
        assert result.metadata["prompt_mismatch"] is True
        assert (tmp_path / "out.png").read_bytes() == png
