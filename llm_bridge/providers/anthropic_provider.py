#!/usr/bin/env python3
"""
Anthropic Claude LLM provider.

Uses the Anthropic Messages API with httpx for async streaming support.
Handles system prompt extraction (Anthropic uses a separate ``system`` field).
"""

import json
import os
from typing import AsyncGenerator, Dict, List, Optional, Union

from core.logger import get_logger
from llm_bridge.providers.base import LLMProvider

log = get_logger("llm.anthropic")

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider.

    Parameters
    ----------
    api_key : str
        Anthropic API key. Falls back to ``ANTHROPIC_API_KEY`` env var.
    model : str
        Model identifier (e.g. ``claude-3-haiku-20240307``).
    temperature : float
        Sampling temperature.
    max_tokens : int
        Maximum tokens in response.
    top_p : float
        Nucleus sampling parameter.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-3-haiku-20240307",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        top_p: float = 1.0,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self._base_url = "https://api.anthropic.com/v1/messages"

    async def chat(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        **kwargs,
    ) -> Union[AsyncGenerator[str, None], str]:
        """Send a chat completion request to Claude."""
        if not _HTTPX_AVAILABLE:
            raise RuntimeError("httpx is required. Install: pip install httpx")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        # Extract system message (Anthropic uses separate field)
        system = ""
        chat_msgs: List[Dict[str, str]] = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                chat_msgs.append(m)

        payload: Dict = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "messages": chat_msgs,
        }
        if system:
            payload["system"] = system

        if stream:
            payload["stream"] = True
            return self._stream_chat(headers, payload)
        else:
            return await self._complete_chat(headers, payload)

    async def _complete_chat(
        self, headers: Dict, payload: Dict
    ) -> str:
        """Non-streaming completion."""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(self._base_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                parts = data.get("content", [])
                return "".join(
                    p.get("text", "") for p in parts if p.get("type") == "text"
                )
        except httpx.HTTPStatusError as e:
            log.error(f"Anthropic API error: {e.response.status_code} {e.response.text}")
            raise
        except Exception as e:
            log.error(f"Anthropic request failed: {e}")
            raise

    async def _stream_chat(
        self, headers: Dict, payload: Dict
    ) -> AsyncGenerator[str, None]:
        """Streaming completion via SSE."""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", self._base_url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
                        except (json.JSONDecodeError, KeyError):
                            continue
        except httpx.HTTPStatusError as e:
            log.error(f"Anthropic streaming error: {e.response.status_code}")
            raise
        except Exception as e:
            log.error(f"Anthropic streaming failed: {e}")
            raise

    def is_available(self) -> bool:
        """Return True if API key is configured."""
        return bool(self.api_key)

    def get_model_name(self) -> str:
        """Return the model identifier."""
        return self.model
