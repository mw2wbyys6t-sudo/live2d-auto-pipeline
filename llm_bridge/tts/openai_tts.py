#!/usr/bin/env python3
"""OpenAI TTS provider (tts-1 / tts-1-hd models)."""

import os
from typing import Optional, Union

from core.logger import get_logger
from llm_bridge.tts.base import TTSProvider

log = get_logger("tts.openai")

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore


class OpenAITTSProvider(TTSProvider):
    """OpenAI text-to-speech provider.

    Parameters
    ----------
    api_key : str or None
        OpenAI API key. Falls back to ``OPENAI_API_KEY`` env var.
    base_url : str
        API base URL (can be redirected to compatible providers).
    model : str
        TTS model (``tts-1`` or ``tts-1-hd``).
    voice : str
        Voice name (``alloy``, ``echo``, ``fable``, ``onyx``, ``nova``, ``shimmer``).
    response_format : str
        Audio format (``mp3``, ``opus``, ``aac``, ``flac``, ``wav``).
    """

    VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "tts-1",
        voice: str = "nova",
        response_format: str = "mp3",
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice if voice in self.VALID_VOICES else "nova"
        self.response_format = response_format

    async def synthesize(
        self, text: str, output_path: Optional[str] = None
    ) -> Union[bytes, str]:
        """Synthesize speech via OpenAI TTS API.

        Parameters
        ----------
        text : str
            Text to speak.
        output_path : str or None
            If provided, save audio to this path. Otherwise return bytes.

        Returns
        -------
        bytes or str
        """
        if not _HTTPX_AVAILABLE:
            raise RuntimeError("httpx is required. Install: pip install httpx")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        if not text or not text.strip():
            return b"" if output_path is None else output_path

        url = f"{self.base_url}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": self.response_format,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                audio = resp.content
        except httpx.HTTPStatusError as e:
            log.error(f"OpenAI TTS error: {e.response.status_code} {e.response.text}")
            raise
        except Exception as e:
            log.error(f"OpenAI TTS failed: {e}")
            raise

        if output_path:
            with open(output_path, "wb") as f:
                f.write(audio)
            log.debug(f"OpenAI TTS saved to {output_path}")
            return output_path

        return audio

    def is_available(self) -> bool:
        """Return True if API key is configured."""
        return bool(self.api_key)
