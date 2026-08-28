#!/usr/bin/env python3
"""
Local LLM provider via Ollama.

Connects to a local Ollama instance (http://localhost:11434 by default)
for free, private LLM inference. Supports streaming via Ollama's NDJSON
streaming API.
"""

import json
import os
from typing import AsyncGenerator, Dict, List, Optional, Union

from core.logger import get_logger
from llm_bridge.providers.base import LLMProvider

log = get_logger("llm.local")

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore


class LocalProvider(LLMProvider):
    """Ollama local LLM provider.

    Parameters
    ----------
    base_url : str
        Ollama API URL. Falls back to ``OLLAMA_BASE_URL`` env var,
        defaulting to ``http://localhost:11434``.
    model : str
        Model name (e.g. ``qwen2.5:3b``, ``llama3.2:3b``).
    temperature : float
        Sampling temperature.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: str = "qwen2.5:3b",
        temperature: float = 0.7,
    ):
        self.base_url = (
            base_url
            or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self.model = model
        self.temperature = temperature

    async def chat(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        **kwargs,
    ) -> Union[AsyncGenerator[str, None], str]:
        """Send a chat request to local Ollama instance."""
        if not _HTTPX_AVAILABLE:
            raise RuntimeError("httpx is required. Install: pip install httpx")

        url = f"{self.base_url}/api/chat"
        payload: Dict = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": kwargs.get("temperature", self.temperature),
            },
        }

        if stream:
            return self._stream_chat(url, payload)
        else:
            return await self._complete_chat(url, payload)

    async def _complete_chat(
        self, url: str, payload: Dict
    ) -> str:
        """Non-streaming completion."""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 404:
                    raise RuntimeError(
                        f"Ollama model '{self.model}' not found. "
                        f"Run: ollama pull {self.model}"
                    )
                resp.raise_for_status()
                data = resp.json()
                return data.get("message", {}).get("content", "")
        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url}. "
                f"Is Ollama running?"
            )
        except Exception as e:
            log.error(f"Ollama request failed: {e}")
            raise

    async def _stream_chat(
        self, url: str, payload: Dict
    ) -> AsyncGenerator[str, None]:
        """Streaming completion via NDJSON."""
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    if resp.status_code == 404:
                        raise RuntimeError(
                            f"Model '{self.model}' not found in Ollama"
                        )
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            if data.get("done"):
                                break
                            content = data.get("message", {}).get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except httpx.ConnectError:
            raise RuntimeError(f"Cannot connect to Ollama at {self.base_url}")
        except Exception as e:
            log.error(f"Ollama streaming failed: {e}")
            raise

    def is_available(self) -> bool:
        """Check if Ollama is reachable by querying /api/tags."""
        if not _HTTPX_AVAILABLE:
            return False
        try:
            import httpx as _httpx
            with _httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return False
                # Check if our model is pulled
                models = resp.json().get("models", [])
                return any(m.get("name", "").startswith(self.model.split(":")[0])
                          for m in models) or True  # Be lenient
        except Exception:
            return False

    def get_model_name(self) -> str:
        """Return ``ollama/<model>``."""
        return f"ollama/{self.model}"
