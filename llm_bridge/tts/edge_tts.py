#!/usr/bin/env python3
"""
Microsoft Edge TTS provider (free, no API key required).

Uses the ``edge-tts`` library to synthesize speech via Microsoft Edge's
online TTS engine. Supports Chinese, Japanese, English, and many other
voices out of the box.
"""

import os
import tempfile
from typing import Optional, Union, List

from core.logger import get_logger
from llm_bridge.tts.base import TTSProvider

log = get_logger("tts.edge")

try:
    import edge_tts
    _EDGE_AVAILABLE = True
except ImportError:
    _EDGE_AVAILABLE = False
    edge_tts = None  # type: ignore


class EdgeTTSProvider(TTSProvider):
    """Free TTS using Microsoft Edge's online neural TTS engine.

    Parameters
    ----------
    voice : str
        Voice name (e.g. ``zh-CN-XiaoxiaoNeural``, ``ja-JP-NanamiNeural``).
    rate : str
        Speech rate adjustment, e.g. ``"+0%"``, ``"+20%"``, ``"-10%"``.
    volume : str
        Volume adjustment, e.g. ``"+0%"``.
    """

    # Common voice presets
    VOICES = {
        "zh-female": "zh-CN-XiaoxiaoNeural",
        "zh-female-cute": "zh-CN-XiaoyiNeural",
        "zh-female-warm": "zh-CN-XiaohanNeural",
        "zh-male": "zh-CN-YunxiNeural",
        "zh-male-deep": "zh-CN-YunyangNeural",
        "ja-female": "ja-JP-NanamiNeural",
        "ja-female-cute": "ja-JP-AoiNeural",
        "ja-male": "ja-JP-KeitaNeural",
        "en-female": "en-US-AriaNeural",
        "en-female-cheerful": "en-US-JennyNeural",
        "en-male": "en-US-GuyNeural",
        "en-male-deep": "en-US-ChristopherNeural",
        "ko-female": "ko-KR-SunHiNeural",
        "ko-male": "ko-KR-InJoonNeural",
    }

    def __init__(
        self,
        voice: Optional[str] = None,
        rate: str = "+0%",
        volume: str = "+0%",
    ):
        self.voice = voice or os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        self.rate = rate or os.environ.get("TTS_RATE", "+0%")
        self.volume = volume

    async def synthesize(
        self, text: str, output_path: Optional[str] = None
    ) -> Union[bytes, str]:
        """Synthesize speech via Edge TTS.

        Parameters
        ----------
        text : str
            Text to synthesize.
        output_path : str or None
            If provided, save MP3 audio to this path. Otherwise return bytes.

        Returns
        -------
        bytes or str
            Audio bytes (MP3) if output_path is None, else the output path.
        """
        if not _EDGE_AVAILABLE:
            raise RuntimeError(
                "edge-tts not installed. Install with: pip install edge-tts"
            )

        if not text or not text.strip():
            log.warning("Empty text passed to TTS, returning empty")
            return b"" if output_path is None else output_path

        save_path = output_path
        created_tmp = False
        if save_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            save_path = tmp.name
            tmp.close()
            created_tmp = True

        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                volume=self.volume or "+0%",
            )
            await communicate.save(save_path)
            log.debug(f"Edge TTS synthesized {len(text)} chars to {save_path}")

            if output_path is not None:
                return output_path

            with open(save_path, "rb") as f:
                audio_bytes = f.read()
            return audio_bytes

        except Exception as e:
            log.error(f"Edge TTS synthesis failed: {e}")
            raise
        finally:
            if created_tmp and save_path and os.path.exists(save_path):
                try:
                    os.unlink(save_path)
                except OSError:
                    pass

    def is_available(self) -> bool:
        """Return True if edge-tts is installed."""
        return _EDGE_AVAILABLE

    @staticmethod
    def get_available_voices() -> List[str]:
        """Return list of preset voice names."""
        return list(EdgeTTSProvider.VOICES.values())

    @staticmethod
    async def list_online_voices() -> List[dict]:
        """List all available online voices from Edge TTS.

        Returns
        -------
        list[dict]
            Voice metadata dicts from edge-tts.
        """
        if not _EDGE_AVAILABLE:
            return []
        try:
            voices = await edge_tts.list_voices()
            return voices
        except Exception as e:
            log.error(f"Failed to list Edge TTS voices: {e}")
            return []
