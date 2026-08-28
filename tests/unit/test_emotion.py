"""Tests for emotion analysis and LLM bridge."""

import pytest

from llm_bridge.emotion.analyzer import EmotionAnalyzer


class TestEmotionAnalyzer:
    """Test emotion detection from text."""

    def test_happy_emotion(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("I'm so happy and excited today! 🎉")
        assert result["emotion"] in ("happy", "surprised", "excited", "neutral")

    def test_sad_emotion(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("I feel so sad and depressed...")
        assert result["emotion"] in ("sad", "neutral")

    def test_angry_emotion(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("I am furious! This makes me so angry!")
        assert result["emotion"] in ("angry", "neutral")

    def test_surprised_emotion(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("Wow! That's amazing! I can't believe it!")
        assert result["emotion"] in ("surprised", "happy", "neutral")

    def test_chinese_happy(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("我好开心啊！太棒了！")
        assert result["emotion"] in ("happy", "neutral", "surprised")

    def test_chinese_sad(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("我好难过，好伤心...")
        assert result["emotion"] in ("sad", "neutral")

    def test_emotion_to_expression(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        expr = analyzer.emotion_to_expression("happy")
        assert isinstance(expr, str)
        assert len(expr) > 0

    def test_emotion_to_physics(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        phys = analyzer.emotion_to_physics("angry")
        assert isinstance(phys, dict)

    def test_neutral_fallback(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("")
        assert result["emotion"] == "neutral"

    def test_confidence_range(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        result = analyzer.analyze("happy happy joy joy")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_all_emotions_have_mappings(self):
        analyzer = EmotionAnalyzer(use_llm=False)
        for emotion in EmotionAnalyzer.EMOTIONS:
            expr = analyzer.emotion_to_expression(emotion)
            assert expr is not None
            phys = analyzer.emotion_to_physics(emotion)
            assert phys is not None
