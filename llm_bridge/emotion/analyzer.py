#!/usr/bin/env python3
"""
Emotion analysis from text for Live2D expression driving.

Supports two modes:
1. Rule-based quick analysis using keyword dictionaries (no dependencies, instant)
2. LLM-based deep analysis for nuanced emotion detection (optional, async)

Maps 7 emotions to Live2D expression names and body language parameters.
"""

import json
import re
from typing import Dict, List, Optional

from core.logger import get_logger

log = get_logger("emotion")


class EmotionAnalyzer:
    """Analyze text to determine emotion, then map to Live2D expressions.

    Parameters
    ----------
    use_llm : bool
        If True, use LLM for emotion analysis when available (async analyze()).
        Falls back to keyword matching always.
    llm_provider : optional
        An LLMProvider instance for LLM-based analysis. If None, only
        keyword-based analysis is used.
    """

    EMOTIONS: List[str] = [
        "happy", "sad", "angry", "calm", "shy", "surprised", "neutral",
    ]

    # Keyword dictionaries for rule-based analysis (Chinese + English)
    EMOTION_KEYWORDS: Dict[str, List[str]] = {
        "happy": [
            # English
            "happy", "joy", "yay", "great", "awesome", "wonderful", "love",
            "excited", "glad", "cheerful", "delighted", "amazing", "fantastic",
            "excellent", "haha", "lol", "lmao", "funny", "cute", "beautiful",
            "nice", "good", "perfect", "yay", "woohoo",
            # Chinese
            "开心", "快乐", "高兴", "好棒", "太好了", "喜欢", "可爱", "哈哈",
            "嘻嘻", "兴奋", "满足", "幸福", "愉快", "赞", "不错", "厉害",
            "漂亮", "真棒", "耶", "嘿嘿", "好呀", "好的", "满意", "谢谢你",
            "好开心", "超棒", "爱你",
        ],
        "sad": [
            "sad", "unhappy", "cry", "crying", "tears", "depressed", "lonely",
            "miss", "sorry", "regret", "disappointed", "hurt", "pain", "suffer",
            "hopeless", "gloomy", "miserable",
            "难过", "伤心", "哭", "泪", "委屈", "孤独", "寂寞", "想你", "遗憾",
            "失望", "痛苦", "难受", "可怜", "呜呜", "悲伤", "忧伤", "心碎",
            "不想", "唉", "哎",
        ],
        "angry": [
            "angry", "mad", "furious", "rage", "hate", "annoyed", "frustrated",
            "stupid", "dumb", "damn", "hell", "shut up", "ugh", "irritated",
            "pissed", "wtf", "outrageous",
            "生气", "愤怒", "气死", "讨厌", "可恶", "该死", "烦", "混蛋",
            "蠢", "笨蛋", "受不了", "哼", "气", "恼火", "气死我了", "可恶",
            "滚", "闭嘴",
        ],
        "shy": [
            "shy", "blush", "embarrassed", "nervous", "awkward", "flustered",
            "bashful", "timid",
            "不好意思", "害羞", "脸红", "羞", "尴尬", "紧张", "难为情",
            "哎呀", "别这样", "讨厌啦", "难为情", "羞羞", "人家",
        ],
        "surprised": [
            "wow", "whoa", "omg", "oh no", "what", "really", "no way",
            "incredible", "unbelievable", "shocked", "surprised", "astonished",
            "wait", "huh", "whoa",
            "哇", "天哪", "不会吧", "真的假的", "什么", "居然", "竟然",
            "哎", "诶", "没想到", "震惊", "惊讶", "厉害", "我的天",
            "太意外了", "怎么回事",
        ],
        "calm": [
            "calm", "peaceful", "relaxed", "okay", "fine", "alright", "sure",
            "yes", "indeed", "certainly", "of course",
            "平静", "还好", "还行", "嗯嗯", "好的", "知道", "明白",
            "理解", "当然", "可以", "行", "没问题", "了解了",
        ],
    }

    # Emotion -> Live2D expression name
    EMOTION_TO_EXPRESSION: Dict[str, str] = {
        "happy": "happy",
        "sad": "sad",
        "angry": "angry",
        "calm": "normal",
        "shy": "shy",
        "surprised": "surprised",
        "neutral": "normal",
    }

    # Emotion -> body language / physics parameters
    EMOTION_TO_PHYSICS: Dict[str, Dict[str, float]] = {
        "happy": {
            "ParamBodyAngleX": 2.0,
            "ParamBreath": 0.6,
            "ParamMouthForm": 0.8,
            "breathing_rate": 1.2,
            "sway_amplitude": 4.0,
        },
        "sad": {
            "ParamBodyAngleX": -3.0,
            "ParamBreath": 0.25,
            "ParamBrowLY": -0.5,
            "ParamBrowRY": -0.5,
            "breathing_rate": 0.7,
            "sway_amplitude": 1.5,
        },
        "angry": {
            "ParamBrowLY": 0.8,
            "ParamBrowRY": 0.8,
            "ParamMouthForm": -0.5,
            "breathing_rate": 1.5,
            "sway_amplitude": 2.0,
        },
        "calm": {
            "ParamBodyAngleX": 0.0,
            "ParamBreath": 0.4,
            "breathing_rate": 0.9,
            "sway_amplitude": 2.5,
        },
        "shy": {
            "ParamCheek": 0.7,
            "ParamBrowLY": -0.3,
            "ParamBrowRY": -0.3,
            "ParamBodyAngleX": -1.0,
            "breathing_rate": 1.0,
            "sway_amplitude": 1.0,
        },
        "surprised": {
            "ParamEyeLOpen": 1.0,
            "ParamEyeROpen": 1.0,
            "ParamMouthOpenY": 0.6,
            "ParamBrowLY": 0.5,
            "ParamBrowRY": 0.5,
            "breathing_rate": 1.8,
            "sway_amplitude": 0.5,
        },
        "neutral": {
            "ParamBreath": 0.5,
            "breathing_rate": 1.0,
            "sway_amplitude": 3.0,
        },
    }

    # Visual (face) parameters used for Top-2 emotion mixing. These mirror
    # the desktop-pet EXPRESSIONS keys so mixed_physics can drive mouth
    # shape, eye openness and blush for blended emotions (e.g. "happy+shy").
    EMOTION_VISUAL_PARAMS: Dict[str, Dict[str, float]] = {
        "happy": {"mouth_scale_y": 1.15, "mouth_scale_x": 1.1, "eye_open": 0.7,
                  "eye_scale_x": 0.9, "eyebrow_angle": -0.15, "blush": 0.6},
        "shy": {"mouth_scale_y": 0.85, "mouth_scale_x": 0.9, "eye_open": 0.5,
                "eye_scale_x": 0.8, "eyebrow_angle": -0.2, "blush": 0.8},
        "sad": {"mouth_scale_y": 0.9, "mouth_scale_x": 0.85, "eye_open": 0.8,
                "eye_scale_x": 0.95, "eyebrow_angle": -0.1, "blush": 0.0},
        "angry": {"mouth_scale_y": 0.7, "mouth_scale_x": 0.85, "eye_open": 0.9,
                  "eye_scale_x": 1.1, "eyebrow_angle": 0.3, "blush": 0.4},
        "surprised": {"mouth_scale_y": 1.3, "mouth_scale_x": 1.15, "eye_open": 1.2,
                      "eye_scale_x": 1.1, "eyebrow_angle": 0.2, "blush": 0.0},
        "calm": {"mouth_scale_y": 1.0, "mouth_scale_x": 1.0, "eye_open": 1.0,
                 "eye_scale_x": 1.0, "eyebrow_angle": 0.0, "blush": 0.0},
        "neutral": {"mouth_scale_y": 1.0, "mouth_scale_x": 1.0, "eye_open": 1.0,
                    "eye_scale_x": 1.0, "eyebrow_angle": 0.0, "blush": 0.0},
    }

    # Emotion -> TTS prosody parameters (rate, pitch)
    EMOTION_TO_PROSODY: Dict[str, Dict[str, str]] = {
        "happy": {"rate": "+15%", "pitch": "+10%"},
        "sad": {"rate": "-10%", "pitch": "-15%"},
        "angry": {"rate": "+5%", "pitch": "-5%"},
        "calm": {"rate": "+0%", "pitch": "+0%"},
        "shy": {"rate": "-5%", "pitch": "+5%"},
        "surprised": {"rate": "+20%", "pitch": "+15%"},
        "neutral": {"rate": "+0%", "pitch": "+0%"},
    }

    def __init__(self, use_llm: bool = True, llm_provider=None):
        self.use_llm = use_llm
        self.llm_provider = llm_provider

    def analyze(self, text: str) -> Dict:
        """Analyze text emotion (synchronous, keyword-based).

        For LLM-based analysis, use ``analyze_async()``.

        Parameters
        ----------
        text : str
            Text to analyze.

        Returns
        -------
        dict
            ``{"emotion": str, "confidence": float, "intensity": float,
               "expression": str, "scores": dict}``
        """
        if not text or not text.strip():
            return self._neutral_result()

        text_lower = text.lower()

        # Count keyword matches per emotion
        scores: Dict[str, int] = {e: 0 for e in self.EMOTIONS}
        for emotion, keywords in self.EMOTION_KEYWORDS.items():
            for kw in keywords:
                kw_lower = kw.lower()
                # Case-insensitive English match
                count = text_lower.count(kw_lower)
                if count > 0:
                    scores[emotion] += count
                # Original case match (for Chinese characters)
                if kw != kw_lower and kw in text:
                    scores[emotion] += text.count(kw)

        # Punctuation-based signals
        if "!" in text or "！" in text:
            scores["surprised"] += 1
            scores["happy"] += 1
        if "?" in text or "？" in text:
            scores["surprised"] += 1
        if "..." in text or "。。。" in text or "……" in text:
            scores["sad"] += 1
            scores["calm"] -= 0  # neutral
        if re.search(r"[～~]", text):
            scores["happy"] += 1
            scores["shy"] += 1
        # Emoticons / emoji-like patterns
        if any(e in text for e in ["😊", "😄", "🥰", "❤️", "✨", "(≧▽≦)"]):
            scores["happy"] += 2
        if any(e in text for e in ["😢", "😭", "💔", "(╥_╥)", "T_T"]):
            scores["sad"] += 2
        if any(e in text for e in ["😠", "😡", "💢", "(╬▔皿▔)"]):
            scores["angry"] += 2
        if any(e in text for e in ["😳", "🥺", "(//▽//)"]):
            scores["shy"] += 2
        if any(e in text for e in ["😲", "😱", "(°□°)"]):
            scores["surprised"] += 2

        total = sum(max(0, v) for v in scores.values())
        if total == 0:
            return self._neutral_result()

        # Find dominant emotion
        dominant = max(scores, key=lambda k: scores[k])
        confidence = scores[dominant] / total
        intensity = min(1.0, scores[dominant] / 5.0)

        expression = self.emotion_to_expression(dominant)

        # Top-2 emotion mixing: keep the dominant emotion for backward
        # compatibility but also expose the runner-up and a blended set of
        # physics/visual parameters so expressions like "70% happy + 30% shy"
        # can be rendered instead of a hard single-emotion switch.
        ranked = sorted(
            ((e, max(0, scores[e])) for e in self.EMOTIONS),
            key=lambda kv: kv[1],
            reverse=True,
        )
        e1, s1 = ranked[0]
        if len(ranked) > 1 and ranked[1][1] > 0:
            e2, s2 = ranked[1]
        else:
            e2, s2 = e1, 0.0
        secondary_confidence = (s2 / total) if total > 0 else 0.0
        mixed_physics = self._mix_emotion_params(e1, s1, e2, s2)

        log.debug(
            f"Emotion: '{text[:40]}...' -> {dominant} "
            f"(conf={confidence:.2f}, int={intensity:.2f}, "
            f"mix={e1}/{e2})"
        )

        return {
            "emotion": dominant,
            "confidence": round(confidence, 3),
            "intensity": round(intensity, 3),
            "expression": expression,
            "scores": {k: v for k, v in scores.items() if v > 0},
            "secondary_emotion": e2 if s2 > 0 else e1,
            "secondary_confidence": round(secondary_confidence, 3),
            "mixed_physics": mixed_physics,
        }

    def _merge_emotion_params(self, emotion: str) -> Dict[str, float]:
        """Combine physics and visual parameters for one emotion."""
        merged: Dict[str, float] = {}
        merged.update(self.EMOTION_TO_PHYSICS.get(emotion, {}))
        merged.update(self.EMOTION_VISUAL_PARAMS.get(emotion, self.EMOTION_VISUAL_PARAMS["neutral"]))
        return merged

    def _mix_emotion_params(
        self,
        e1: str,
        s1: float,
        e2: str,
        s2: float,
    ) -> Dict[str, float]:
        """Blend two emotions' physics/visual params by their score ratio.

        Numeric parameters are linearly interpolated; non-numeric values
        follow the dominant emotion once its weight exceeds 0.5.
        """
        p1 = self._merge_emotion_params(e1)
        p2 = self._merge_emotion_params(e2)
        total = s1 + s2
        w1 = (s1 / total) if total > 0 else 1.0
        w2 = 1.0 - w1

        mixed: Dict[str, float] = {}
        for key in set(p1) | set(p2):
            v1 = p1.get(key)
            v2 = p2.get(key, v1)
            if v1 is None:
                v1 = v2
            if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                mixed[key] = round(float(v1) * w1 + float(v2) * w2, 4)
            else:
                mixed[key] = v1 if w1 > 0.5 else v2
        return mixed

    async def analyze_async(self, text: str) -> Dict:
        """Analyze text emotion using LLM if available, falling back to keywords.

        Returns
        -------
        dict
            Same format as ``analyze()``.
        """
        if not self.use_llm or self.llm_provider is None:
            return self.analyze(text)

        # Try LLM-based analysis
        try:
            prompt = (
                f"Analyze the emotion of this text. "
                f"Respond with ONLY a JSON object with keys: "
                f"emotion (one of: happy, sad, angry, calm, shy, surprised, neutral), "
                f"confidence (0-1), intensity (0-1).\n\n"
                f"Text: {text}\n\nJSON:"
            )
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm_provider.chat(messages, stream=False)
            if isinstance(response, str):
                # Parse JSON from response
                json_match = re.search(r'\{[^}]+\}', response)
                if json_match:
                    data = json.loads(json_match.group())
                    emotion = data.get("emotion", "neutral")
                    if emotion in self.EMOTIONS:
                        return {
                            "emotion": emotion,
                            "confidence": float(data.get("confidence", 0.5)),
                            "intensity": float(data.get("intensity", 0.5)),
                            "expression": self.emotion_to_expression(emotion),
                            "scores": {emotion: 1},
                        }
        except Exception as e:
            log.debug(f"LLM emotion analysis failed, using keywords: {e}")

        # Fallback
        return self.analyze(text)

    def analyze_simple(self, text: str) -> str:
        """Quick rule-based analysis returning just the emotion name.

        Parameters
        ----------
        text : str
            Text to analyze.

        Returns
        -------
        str
            Emotion name (e.g. ``"happy"``).
        """
        return self.analyze(text)["emotion"]

    def emotion_to_expression(self, emotion: str) -> str:
        """Map an emotion name to a Live2D expression name.

        Parameters
        ----------
        emotion : str
            Emotion identifier.

        Returns
        -------
        str
            Live2D expression name.
        """
        return self.EMOTION_TO_EXPRESSION.get(emotion, "normal")

    def emotion_to_physics(self, emotion: str) -> Dict[str, float]:
        """Map an emotion to body language / physics parameters.

        Parameters
        ----------
        emotion : str
            Emotion identifier.

        Returns
        -------
        dict[str, float]
            Parameter name -> value overrides.
        """
        return dict(self.EMOTION_TO_PHYSICS.get(emotion, self.EMOTION_TO_PHYSICS["neutral"]))

    def emotion_to_prosody(self, emotion: str) -> Dict[str, str]:
        """Map an emotion to TTS prosody settings (rate, pitch).

        Parameters
        ----------
        emotion : str
            Emotion identifier.

        Returns
        -------
        dict[str, str]
            TTS prosody parameters.
        """
        return dict(self.EMOTION_TO_PROSODY.get(emotion, self.EMOTION_TO_PROSODY["neutral"]))

    def _neutral_result(self) -> Dict:
        """Return a neutral emotion result."""
        return {
            "emotion": "neutral",
            "confidence": 0.0,
            "intensity": 0.0,
            "expression": "normal",
            "scores": {},
            "secondary_emotion": "neutral",
            "secondary_confidence": 0.0,
            "mixed_physics": self._mix_emotion_params("neutral", 1.0, "neutral", 0.0),
        }
