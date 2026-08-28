#!/usr/bin/env python3
"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Dict, Union


class LLMProvider(ABC):
    """Abstract LLM provider interface.

    Implementations must provide ``chat()`` for both one-shot and streaming
    responses, plus availability and identification methods.
    """

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        **kwargs,
    ) -> Union[AsyncGenerator[str, None], str]:
        """Send a chat completion request.

        Parameters
        ----------
        messages : list[dict]
            OpenAI-format message list:
            ``[{"role": "user"|"assistant"|"system", "content": str}]``.
        stream : bool
            If True, return an async generator yielding text chunks.
            If False, return the complete response string.
        **kwargs
            Provider-specific parameters (temperature, max_tokens, top_p, etc.).

        Returns
        -------
        AsyncGenerator[str, None] or str
        """
        ...

    async def chat_stream(
        self, messages: List[Dict[str, str]], **kwargs
    ) -> AsyncGenerator[str, None]:
        """Convenience async generator for streaming responses.

        Calls ``chat(messages, stream=True, **kwargs)`` and yields chunks.
        """
        result = await self.chat(messages, stream=True, **kwargs)
        if isinstance(result, str):
            yield result
        else:
            async for chunk in result:
                yield chunk

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider is configured and reachable."""
        ...

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the model identifier string."""
        ...
