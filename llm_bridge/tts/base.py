#!/usr/bin/env python3
"""Abstract base class for TTS (text-to-speech) providers."""

from abc import ABC, abstractmethod
from typing import Optional, Union


class TTSProvider(ABC):
    """Base class for text-to-speech providers.

    Implementations synthesize speech from text and return either raw
    audio bytes or a file path to the saved audio.
    """

    @abstractmethod
    async def synthesize(
        self, text: str, output_path: Optional[str] = None
    ) -> Union[bytes, str]:
        """Synthesize speech from text.

        Parameters
        ----------
        text : str
            The text to speak.
        output_path : str or None
            If provided, save audio to this path and return it.
            If None, return raw audio bytes.

        Returns
        -------
        bytes or str
            Audio bytes (if output_path is None) or the output path string.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this TTS provider is configured and usable."""
        ...
