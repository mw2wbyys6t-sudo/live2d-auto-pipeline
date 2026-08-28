#!/usr/bin/env python3
"""
FunASR provider - Chinese-optimized speech recognition.

Uses the FunASR toolkit (Paraformer, SenseVoice, etc.) which excels at
Chinese anime/character voice recognition. Model is loaded lazily.
"""

import os
import tempfile
from typing import Optional, Union

from core.logger import get_logger
from llm_bridge.asr.base import ASRProvider

log = get_logger("asr.funasr")

try:
    from funasr import AutoModel
    _FUNASR_AVAILABLE = True
except ImportError:
    _FUNASR_AVAILABLE = False
    AutoModel = None  # type: ignore


class FunASRProvider(ASRProvider):
    """FunASR Chinese speech recognition provider.

    Parameters
    ----------
    model : str
        Model name. Common options:
        - ``"paraformer-zh"``: General Chinese model
        - ``"iic/SenseVoiceSmall"``: Multi-lingual with emotion detection
        - ``"paraformer-zh-streaming"``: Streaming model
    device : str
        Device: ``"cpu"``, ``"cuda:0"``, etc.
    vad_model : str or None
        Optional VAD model for silence filtering.
    """

    def __init__(
        self,
        model: str = "paraformer-zh",
        device: str = "cpu",
        vad_model: Optional[str] = None,
    ):
        self.model_name = model
        self.device = device
        self.vad_model = vad_model
        self._model = None

    def _load_model(self) -> None:
        """Lazily load the FunASR model."""
        if self._model is not None:
            return

        if not _FUNASR_AVAILABLE:
            raise RuntimeError(
                "FunASR not installed. Install with: pip install funasr"
            )

        try:
            kwargs = dict(model=self.model_name, device=self.device)
            if self.vad_model:
                kwargs["vad_model"] = self.vad_model
            self._model = AutoModel(**kwargs)
            log.success(f"FunASR loaded: {self.model_name} on {self.device}")
        except Exception as e:
            log.error(f"Failed to load FunASR model '{self.model_name}': {e}")
            raise

    async def transcribe(
        self,
        audio_data: Union[bytes, str],
        language: str = "zh",
    ) -> str:
        """Transcribe audio using FunASR.

        Parameters
        ----------
        audio_data : bytes or str
            Raw audio bytes or path to audio file.
        language : str
            Language hint (default ``"zh"`` for Chinese).

        Returns
        -------
        str
            Transcribed text.
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
            result = self._model.generate(input=audio_path)
            if result and isinstance(result, list):
                text = result[0].get("text", "").strip()
                log.debug(f"FunASR transcribed: {text[:80]}...")
                return text
            return ""
        except Exception as e:
            log.error(f"FunASR transcription failed: {e}")
            return ""
        finally:
            if cleanup and os.path.exists(str(audio_path)):
                try:
                    os.unlink(str(audio_path))
                except OSError:
                    pass

    def is_available(self) -> bool:
        """Return True if funasr is installed."""
        return _FUNASR_AVAILABLE
