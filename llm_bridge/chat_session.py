#!/usr/bin/env python3
"""
Chat session manager for Live2D desktop pet characters.

Full pipeline: text/voice input -> LLM -> emotion analysis -> TTS ->
expression + audio output. Each response chunk carries text, emotion,
expression, and optional audio for real-time character animation.

Supports:
- Text input -> LLM -> emotion -> TTS -> expression + audio
- Voice input -> ASR -> LLM -> emotion -> TTS -> expression + audio
- Character persona system prompt (Chinese/English bilingual)
- Conversation history with size cap
- Voice command parsing (hide, change outfit, expressions, etc.)
"""

from typing import AsyncGenerator, Optional, Callable, Dict, List, Any

from core.logger import get_logger
from llm_bridge.emotion.analyzer import EmotionAnalyzer

log = get_logger("chat_session")

# ---------------------------------------------------------------------------
# Voice command patterns (Chinese + English)
# ---------------------------------------------------------------------------
VOICE_COMMANDS: Dict[str, List[str]] = {
    "hide_pet": [
        "收起桌宠", "隐藏桌宠", "躲起来", "不见了",
        "hide", "disappear", "go away", "minimize",
    ],
    "show_pet": [
        "出来", "显示", "现身",
        "show yourself", "come back", "appear",
    ],
    "change_outfit": [
        "换衣服", "切换穿搭", "换装", "换造型",
        "change outfit", "change clothes", "switch outfit",
    ],
    "change_expression": [
        "笑一个", "开心点", "生气", "难过", "惊讶", "害羞",
        "smile", "be happy", "be angry", "be sad", "surprised",
    ],
    "stop_talking": [
        "别说了", "闭嘴", "安静", "停",
        "stop talking", "be quiet", "shut up", "stop",
    ],
    "dance": [
        "跳舞", "跳个舞", "dance",
    ],
    "wave": [
        "挥手", "招手", "挥挥手", "wave", "wave hello",
    ],
    "repeat": [
        "再说一遍", "重复", "什么",
        "repeat that", "what did you say", "again",
    ],
}

# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE: str = (
    "你是 {name}，一个可爱的虚拟动漫桌宠角色。\n\n"
    "你的性格：{persona}\n\n"
    "你的说话风格：\n"
    "- 用简短、可爱的回答（每句话通常1-3句）\n"
    "- 使用表情符号和语气词（呀、呢、哇、嘿嘿）\n"
    "- 保持活泼可爱的人设\n"
    "- 中文回答为主，用户用英文则用英文回答\n"
    "- 可以偶尔使用可爱的颜文字（如 (≧▽≦)、(*^▽^*)）\n"
    "- 你知道自己是一个桌宠，可以偶尔提及\n"
    "- 不要打破第四面墙，不要说自己是AI\n"
)


class ChatSession:
    """Manages a conversational session with the Live2D character.

    Parameters
    ----------
    llm_router : LLMRouter
        The LLM router for chat completions.
    tts : TTSProvider or None
        TTS provider for speech synthesis.
    asr : ASRProvider or None
        ASR provider for voice transcription.
    emotion : EmotionAnalyzer or None
        Emotion analyzer. Defaults to a keyword-only analyzer.
    character_persona : str
        Personality description for the system prompt.
    character_name : str
        Character display name.
    """

    def __init__(
        self,
        llm_router,
        tts=None,
        asr=None,
        emotion: Optional[EmotionAnalyzer] = None,
        character_persona: str = "活泼可爱，有点傲娇，喜欢和主人聊天",
        character_name: str = "小奈",
    ):
        self.llm = llm_router
        self.tts = tts
        self.asr = asr
        self.emotion = emotion or EmotionAnalyzer(use_llm=False)
        self.character_name = character_name
        self.character_persona = character_persona

        self.history: List[Dict[str, str]] = []
        self.system_prompt: str = SYSTEM_PROMPT_TEMPLATE.format(
            name=character_name, persona=character_persona
        )
        self.current_emotion: str = "neutral"
        self.current_expression: str = "normal"

        # Optional callbacks for UI integration
        self.on_emotion_change: Optional[Callable[[str, str, dict], None]] = None
        self.on_audio_ready: Optional[Callable[[Any], None]] = None

    # ------------------------------------------------------------------
    # Persona / history
    # ------------------------------------------------------------------

    def set_persona(self, persona: str, name: Optional[str] = None) -> None:
        """Update character persona and optionally name.

        Parameters
        ----------
        persona : str
            New personality description.
        name : str or None
            New character name (unchanged if None).
        """
        self.character_persona = persona
        if name:
            self.character_name = name
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            name=self.character_name, persona=persona
        )
        log.info(f"Persona updated: {persona[:60]}...")

    def add_context(self, role: str, content: str) -> None:
        """Add a message to conversation history.

        Parameters
        ----------
        role : str
            ``"user"``, ``"assistant"``, or ``"system"``.
        content : str
            Message text.
        """
        if role not in ("user", "assistant", "system"):
            log.warning(f"Invalid role '{role}', defaulting to 'user'")
            role = "user"
        self.history.append({"role": role, "content": content})
        # Cap history size (keep system prompt separate, cap at 50 messages)
        if len(self.history) > 50:
            self.history = self.history[-30:]

    def clear_history(self) -> None:
        """Clear conversation history."""
        self.history = []
        log.info("Chat history cleared")

    def get_history(self) -> List[Dict[str, str]]:
        """Return a copy of conversation history."""
        return list(self.history)

    # ------------------------------------------------------------------
    # Command parsing
    # ------------------------------------------------------------------

    def _check_voice_command(self, text: str) -> Optional[str]:
        """Check if text contains a known voice command.

        Parameters
        ----------
        text : str
            User message.

        Returns
        -------
        str or None
            Command name if matched, else None.
        """
        text_lower = text.lower().strip().rstrip("。！？!?~～")
        for cmd, phrases in VOICE_COMMANDS.items():
            for phrase in phrases:
                if phrase.lower() in text_lower or phrase in text:
                    log.info(f"Voice command detected: {cmd}")
                    return cmd
        return None

    # ------------------------------------------------------------------
    # Text chat
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Send a text message and stream the response.

        Parameters
        ----------
        text : str
            User's message.

        Yields
        ------
        dict
            Chunks with ``type`` field:
            - ``"command"``: voice command detected (``{"command": str}``)
            - ``"text"``: incremental text (``{"text": str}``)
            - ``"emotion"``: emotion analysis result
              (``{"emotion", "expression", "confidence", "params"}``)
            - ``"audio"``: TTS audio (``{"audio_path": str/bytes}``)
            - ``"error"``: error message (``{"error": str}``)
        """
        if not text or not text.strip():
            return

        # Check for voice commands first
        cmd = self._check_voice_command(text)
        if cmd:
            yield {"type": "command", "command": cmd, "text": f"好的！({cmd})"}
            return

        # Add user message to history
        self.add_context("user", text)

        # Build messages for LLM
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.history)

        full_response = ""
        try:
            async for chunk in self.llm.chat(messages, stream=True):
                full_response += chunk
                yield {"type": "text", "text": chunk}

            # Store assistant response
            self.add_context("assistant", full_response)

            # Analyze emotion from response
            emotion_result = self.emotion.analyze(full_response)
            emotion = emotion_result["emotion"]
            expression = emotion_result["expression"]
            params = self.emotion.emotion_to_physics(emotion)

            self.current_emotion = emotion
            self.current_expression = expression

            yield {
                "type": "emotion",
                "emotion": emotion,
                "expression": expression,
                "confidence": emotion_result["confidence"],
                "intensity": emotion_result["intensity"],
                "params": params,
            }

            if self.on_emotion_change:
                try:
                    self.on_emotion_change(emotion, expression, params)
                except Exception as e:
                    log.warning(f"on_emotion_change callback error: {e}")

            # Synthesize speech via TTS
            if self.tts and full_response.strip():
                try:
                    prosody = self.emotion.emotion_to_prosody(emotion)
                    audio_result = await self.tts.synthesize(full_response)
                    yield {
                        "type": "audio",
                        "audio": audio_result,
                        "prosody": prosody,
                    }
                    if self.on_audio_ready:
                        try:
                            self.on_audio_ready(audio_result)
                        except Exception as e:
                            log.warning(f"on_audio_ready callback error: {e}")
                except Exception as e:
                    log.warning(f"TTS failed: {e}")
                    yield {"type": "audio", "audio": None, "error": str(e)}

        except Exception as e:
            log.error(f"Chat error: {e}")
            yield {"type": "error", "error": str(e)}

    # ------------------------------------------------------------------
    # Voice chat
    # ------------------------------------------------------------------

    async def send_voice(
        self, audio_data: bytes
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process voice input: ASR -> LLM -> emotion -> TTS.

        Parameters
        ----------
        audio_data : bytes
            Raw audio bytes from microphone.

        Yields
        ------
        dict
            Same format as ``send_message()``, plus an initial
            ``{"type": "transcription", "text": str}`` chunk.
        """
        if not self.asr:
            yield {"type": "error", "error": "ASR not available"}
            return

        try:
            text = await self.asr.transcribe(audio_data)
            if not text or not text.strip():
                yield {"type": "transcription", "text": ""}
                yield {"type": "error", "error": "Could not understand audio"}
                return
            log.info(f"Voice transcription: {text}")
            yield {"type": "transcription", "text": text}
            async for chunk in self.send_message(text):
                yield chunk
        except Exception as e:
            log.error(f"Voice chat error: {e}")
            yield {"type": "error", "error": str(e)}
