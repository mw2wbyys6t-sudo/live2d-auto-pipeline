#!/usr/bin/env python3
"""
Local Whisper / faster-whisper ASR provider.

Uses ``faster-whisper`` (preferred, CTranslate2 backend) with automatic
fallback to OpenAI's ``whisper`` package. Model is loaded lazily on first
transcription to avoid startup overhead.
"""

import os
import tempfile
from typing import Optional, Union

from core.logger import get_logger
from llm_bridge.asr.base import ASRProvider

log = get_logger("asr.whisper")

try:
    from faster_whisper import WhisperModel
    _FASTER_WHISPER_AVAILABLE = True
except ImportError:
    _FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None  # type: ignore

try:
    import whisper as openai_whisper
    _OPENAI_WHISPER_AVAILABLE = True
except ImportError:
    _OPENAI_WHISPER_AVAILABLE = False
    openai_whisper = None  # type: ignore


class WhisperProvider(ASRProvider):
    """Local Whisper ASR provider.

    Parameters
    ----------
    model_size : str
        Model size (``tiny``, ``base``, ``small``, ``medium``, ``large-v3``).
    device : str
        Device to use: ``"auto"``, ``"cpu"``, ``"cuda"``.
    compute_type : str
        Compute precision (faster-whisper only): ``"int8"``, ``"float16"``, etc.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "int8",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._backend: Optional[str] = None  # "faster" or "openai"

    def _load_model(self) -> None:
        """Lazily load the Whisper model."""
        if self._model is not None:
            return

        device = self.device
        if device == "auto":
            # Try CUDA, fall back to CPU
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        if _FASTER_WHISPER_AVAILABLE:
            try:
                self._model = WhisperModel(
                    self.model_size, device=device, compute_type=self.compute_type,
                )
                self._backend = "faster"
                log.success(f"faster-whisper loaded: {self.model_size} on {device}")
                return
            except Exception as e:
                log.warning(f"faster-whisper failed to load: {e}")

        if _OPENAI_WHISPER_AVAILABLE:
            try:
                self._model = openai_whisper.load_model(self.model_size, device=device)
                self._backend = "openai"
                log.success(f"openai-whisper loaded: {self.model_size} on {device}")
                return
            except Exception as e:
                log.warning(f"openai-whisper failed to load: {e}")

        raise RuntimeError(
            "No Whisper backend available. Install one of:\n"
            "  pip install faster-whisper\n"
            "  pip install openai-whisper"
        )

    async def transcribe(
        self,
        audio_data: Union[bytes, str],
        language: str = "zh",
    ) -> str:
        """Transcribe audio to text.

        Parameters
        ----------
        audio_data : bytes or str
            Raw audio bytes (WAV/MP3/etc.) or path to an audio file.
        language : str
            Language code hint (default Chinese).

        Returns
        -------
        str
            Transcribed text, stripped of whitespace.
        """
        self._load_model()

        audio_path = audio_data
        cleanup = False
        if isinstance(audio_data, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(audio_data)
            tmp.close()
            audio_path = tmp.name
            cleanup = True

        try:
            if self._backend == "faster":
                segments, info = self._model.transcribe(
                    audio_path, language=language, vad_filter=True,
                )
                text = " ".join(seg.text for seg in segments).strip()
                log.debug(f"Whisper transcribed ({language}): {text[:80]}...")
                return text
            elif self._backend == "openai":
                result = self._model.transcribe(audio_path, language=language)
                text = result.get("text", "").strip()
                log.debug(f"Whisper transcribed ({language}): {text[:80]}...")
                return text
            else:
                return ""
        except Exception as e:
            log.error(f"Whisper transcription failed: {e}")
            return ""
        finally:
            if cleanup and os.path.exists(str(audio_path)):
                try:
                    os.unlink(str(audio_path))
                except OSError:
                    pass

    def is_available(self) -> bool:
        """Return True if either Whisper backend is installed."""
        return _FASTER_WHISPER_AVAILABLE or _OPENAI_WHISPER_AVAILABLE
