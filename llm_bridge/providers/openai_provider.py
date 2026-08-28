#!/usr/bin/env python3
"""
OpenAI-compatible LLM provider.

Works with OpenAI, DeepSeek, Moonshot, SiliconFlow, Together AI, and any
other service exposing an OpenAI-compatible ``/v1/chat/completions`` endpoint.
Uses httpx for async HTTP with streaming SSE support.
"""

import json
import os
from typing import AsyncGenerator, Dict, List, Optional, Union

from core.logger import get_logger
from llm_bridge.providers.base import LLMProvider

log = get_logger("llm.openai")

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible chat completion provider.

    Parameters
    ----------
    api_key : str
        API key. Falls back to ``OPENAI_API_KEY`` env var.
    base_url : str
        Base URL for the API (default OpenAI, can be DeepSeek, Moonshot, etc.).
    model : str
        Model identifier.
    temperature : float
        Sampling temperature (0..2).
    max_tokens : int
        Maximum tokens in the response.
    top_p : float
        Nucleus sampling parameter.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        top_p: float = 1.0,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p

    async def chat(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        **kwargs,
    ) -> Union[AsyncGenerator[str, None], str]:
        """Send a chat completion request."""
        if not _HTTPX_AVAILABLE:
            raise RuntimeError("httpx is required. Install: pip install httpx")
        if not self.api_key:
            raise RuntimeError("API key not set. Set OPENAI_API_KEY or pass api_key.")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
            "stream": stream,
        }

        if stream:
            return self._stream_chat(url, headers, payload)
        else:
            return await self._complete_chat(url, headers, payload)

    async def _complete_chat(
        self, url: str, headers: Dict, payload: Dict
    ) -> str:
        """Non-streaming chat completion."""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            log.error(f"OpenAI API error: {e.response.status_code} {e.response.text}")
            raise
        except Exception as e:
            log.error(f"OpenAI request failed: {e}")
            raise

    async def _stream_chat(
        self, url: str, headers: Dict, payload: Dict
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion via SSE."""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except httpx.HTTPStatusError as e:
            log.error(f"OpenAI streaming error: {e.response.status_code}")
            raise
        except Exception as e:
            log.error(f"OpenAI streaming failed: {e}")
            raise

    def is_available(self) -> bool:
        """Return True if API key is configured."""
        return bool(self.api_key)

    def get_model_name(self) -> str:
        """Return the model identifier."""
        return self.model
