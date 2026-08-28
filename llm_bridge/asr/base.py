#!/usr/bin/env python3
"""Abstract base class for ASR (automatic speech recognition) providers."""

from abc import ABC, abstractmethod
from typing import Optional, Union


class ASRProvider(ABC):
    """Base class for speech-to-text providers.

    Implementations transcribe audio data (bytes or file path) to text.
    """

    @abstractmethod
    async def transcribe(
        self,
        audio_data: Union[bytes, str],
        language: str = "zh",
    ) -> str:
        """Transcribe audio to text.

        Parameters
        ----------
        audio_data : bytes or str
            Raw audio bytes, or a path to an audio file.
        language : str
            ISO language hint (e.g. ``"zh"``, ``"en"``, ``"ja"``).

        Returns
        -------
        str
            Transcribed text.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this ASR provider is configured and usable."""
        ...
