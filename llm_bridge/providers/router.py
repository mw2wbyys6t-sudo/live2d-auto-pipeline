#!/usr/bin/env python3
"""
LLM provider router with auto-selection and fallback.

Registers multiple LLM providers and routes chat requests to the best
available one. Supports explicit provider selection or automatic fallback
through a priority chain.
"""

import os
from typing import AsyncGenerator, Dict, List, Optional

from core.logger import get_logger
from core.config import config as global_config
from llm_bridge.providers.base import LLMProvider
from llm_bridge.providers.openai_provider import OpenAIProvider
from llm_bridge.providers.anthropic_provider import AnthropicProvider
from llm_bridge.providers.local_provider import LocalProvider

log = get_logger("llm.router")

# Priority order for auto-selection
PROVIDER_PRIORITY = ["openai", "deepseek", "moonshot", "anthropic", "local"]


class LLMRouter:
    """Routes chat requests to available LLM providers with fallback.

    Parameters
    ----------
    config : optional
        Configuration object (SecureConfig instance). If None, uses the
        global singleton from ``core.config``.
    """

    def __init__(self, config=None):
        self.config = config or global_config
        self.providers: Dict[str, LLMProvider] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register default providers from environment/config."""
        # OpenAI-compatible providers (check multiple env vars)
        openai_key = self.config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        openai_url = self.config.get("OPENAI_BASE_URL") or os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        openai_model = self.config.get("LLM_MODEL") or os.environ.get(
            "LLM_MODEL", "gpt-4o-mini"
        )
        if openai_key:
            self.providers["openai"] = OpenAIProvider(
                api_key=openai_key, base_url=openai_url, model=openai_model,
            )

        # DeepSeek
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            self.providers["deepseek"] = OpenAIProvider(
                api_key=deepseek_key,
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
            )

        # Moonshot (Kimi)
        moonshot_key = os.environ.get("MOONSHOT_API_KEY", "")
        if moonshot_key:
            self.providers["moonshot"] = OpenAIProvider(
                api_key=moonshot_key,
                base_url="https://api.moonshot.cn/v1",
                model="moonshot-v1-8k",
            )

        # Anthropic
        anthropic_key = self.config.get("ANTHROPIC_API_KEY") or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        anthropic_model = os.environ.get("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
        if anthropic_key:
            self.providers["anthropic"] = AnthropicProvider(
                api_key=anthropic_key, model=anthropic_model,
            )

        # Local Ollama (always registered, availability checked at request time)
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
        self.providers["local"] = LocalProvider(base_url=ollama_url, model=ollama_model)

        log.info(f"Registered providers: {list(self.providers.keys())}")

    def register_provider(self, name: str, provider: LLMProvider) -> None:
        """Register a custom LLM provider.

        Parameters
        ----------
        name : str
            Unique provider name.
        provider : LLMProvider
            Provider instance.
        """
        self.providers[name] = provider
        log.debug(f"Registered provider: {name}")

    def auto_select(self) -> str:
        """Select the first available provider by priority order.

        Returns the provider name string. Falls back to 'local' if nothing
        else is available (may fail at request time if Ollama isn't running).
        """
        for name in PROVIDER_PRIORITY:
            if name in self.providers and self.providers[name].is_available():
                return name
        return "local"

    def get_available_providers(self) -> List[str]:
        """Return names of currently available providers."""
        return [name for name, p in self.providers.items() if p.is_available()]

    def get_provider(self, name: str) -> Optional[LLMProvider]:
        """Get a provider by name, or None."""
        return self.providers.get(name)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        provider: str = "auto",
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Chat with automatic provider fallback.

        Parameters
        ----------
        messages : list[dict]
            Chat messages in OpenAI format.
        provider : str
            Provider name, or "auto" for automatic selection.
        stream : bool
            If True, yield text chunks; if False, yield a single string.
        **kwargs
            Passed through to the provider's chat method.

        Yields
        ------
        str
            Text chunks (streaming) or complete response (non-streaming).
        """
        if provider == "auto":
            provider = self.auto_select()

        if provider not in self.providers:
            log.warning(f"Provider '{provider}' not registered, trying auto")
            provider = self.auto_select()

        prov = self.providers[provider]
        if not prov.is_available():
            # Try fallback chain
            for fallback in PROVIDER_PRIORITY:
                if fallback != provider and self.providers.get(fallback):
                    if self.providers[fallback].is_available():
                        log.info(f"Falling back from {provider} to {fallback}")
                        provider = fallback
                        prov = self.providers[fallback]
                        break
            else:
                raise RuntimeError(
                    "No LLM provider available. "
                    "Set OPENAI_API_KEY/ANTHROPIC_API_KEY or install Ollama."
                )

        log.info(f"Using LLM provider: {provider} ({prov.get_model_name()})")

        try:
            result = await prov.chat(messages, stream=stream, **kwargs)
            if stream:
                async for chunk in result:
                    yield chunk
            else:
                yield result
        except Exception as e:
            log.error(f"Provider {provider} failed: {e}")
            # Try one more fallback
            if provider != "local":
                log.info("Attempting fallback to local provider")
                try:
                    local = self.providers.get("local")
                    if local and local.is_available():
                        result = await local.chat(messages, stream=stream, **kwargs)
                        if stream:
                            async for chunk in result:
                                yield chunk
                        else:
                            yield result
                        return
                except Exception as e2:
                    log.error(f"Local fallback also failed: {e2}")
            raise
