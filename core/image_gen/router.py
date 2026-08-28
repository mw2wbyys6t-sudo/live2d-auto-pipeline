#!/usr/bin/env python3
"""
Live2D Master Agent - Provider Router (Abstract Provider Registry)

Automatically discovers and routes to the best available image provider.
Priority: seedream > sensenova > pollinations (free fallback).
"""

import os
import time
from pathlib import Path
from typing import Optional, Dict, List, Callable, Type

from core.image_gen.base import ImageProvider, GenerationResult, GenerationError
from core.logger import get_logger

log = get_logger("image_gen")


class ProviderRouter:
    """Routes image generation to the best available provider.

    Provider priority (configurable):
    1. Seedream/ARK (high quality, if key available)
    2. SenseNova (high quality, if key available)
    3. Pollinations.ai (free, always available)
    """

    def __init__(self, config=None):
        from core.config import config as global_config
        self.config = config or global_config
        self._providers: Dict[str, ImageProvider] = {}
        self._registry: Dict[str, Type[ImageProvider]] = {}
        self._on_progress: Optional[Callable[[str, float], None]] = None
        self._register_defaults()

    def _register_defaults(self):
        """Register built-in providers."""
        from core.image_gen.pollinations import PollinationsProvider
        from core.image_gen.sensenova import SenseNovaProvider
        from core.image_gen.seedream import SeedreamProvider
        self.register_provider("pollinations", PollinationsProvider)
        self.register_provider("sensenova", SenseNovaProvider)
        self.register_provider("seedream", SeedreamProvider)

    def register_provider(self, name: str, provider_cls: Type[ImageProvider]):
        """Register a new provider class."""
        self._registry[name] = provider_cls

    def set_progress_callback(self, callback: Callable[[str, float], None]):
        self._on_progress = callback

    def _get_provider(self, name: str) -> Optional[ImageProvider]:
        """Get or create a provider instance."""
        if name not in self._providers and name in self._registry:
            try:
                instance = self._registry[name](config=self.config)
                if self._on_progress:
                    instance.set_progress_callback(self._on_progress)
                self._providers[name] = instance
            except Exception as e:
                log.warning(f"Failed to initialize provider {name}: {e}")
                return None
        return self._providers.get(name)

    def get_available_providers(self) -> List[Dict[str, str]]:
        """Return list of available providers with metadata."""
        result = []
        # Priority order
        priority = ["seedream", "sensenova", "pollinations"]
        for name in priority:
            if name in self._registry:
                p = self._get_provider(name)
                if p and p.is_available():
                    result.append({
                        "name": name,
                        "display_name": p.display_name,
                        "requires_key": p.requires_api_key,
                    })
        return result

    def auto_select(self) -> Optional[ImageProvider]:
        """Auto-select the best available provider."""
        priority = ["seedream", "sensenova", "pollinations"]
        for name in priority:
            p = self._get_provider(name)
            if p and p.is_available():
                log.debug(f"Auto-selected provider: {p.display_name}")
                return p
        return None

    def generate(
        self,
        prompt: str,
        output_path: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        provider: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        auto_fallback: bool = True,
        **kwargs
    ) -> GenerationResult:
        """Generate an image using the specified or auto-selected provider.

        If auto_fallback is True, tries next provider on failure.
        P1-2 FIX: Cleans up temp files on failure.
        """
        # Determine output path
        if output_path is None:
            out_dir = Path(self.config.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = int(time.time())
            output_path = str(out_dir / f"generated_{timestamp}.png")

        # Select provider(s) to try
        if provider:
            providers_to_try = [provider]
        else:
            providers_to_try = ["seedream", "sensenova", "pollinations"]

        last_error = None
        temp_files = set()

        for prov_name in providers_to_try:
            p = self._get_provider(prov_name)
            if p is None or not p.is_available():
                continue

            # Track output for cleanup
            temp_files.add(output_path)

            try:
                log.info(f"Trying provider: {p.display_name}")
                result = p.generate(
                    prompt=prompt,
                    output_path=output_path,
                    width=width,
                    height=height,
                    negative_prompt=negative_prompt,
                    seed=seed,
                    **kwargs
                )
                if result.success:
                    log.success(f"Image generated via {p.display_name}: {result.image_path}")
                    return result
                last_error = GenerationError(result.error or "Unknown error", provider=prov_name)
            except GenerationError as e:
                last_error = e
                log.warning(f"Provider {prov_name} failed: {e}")
                if not auto_fallback or not e.retryable:
                    break
            except Exception as e:
                last_error = GenerationError(str(e), provider=prov_name)
                log.warning(f"Provider {prov_name} error: {e}")

            # P1-2 FIX: Clean up partial output on failure
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass

            if not auto_fallback:
                break

        # All providers failed
        raise GenerationError(
            f"All providers failed. Last error: {last_error}",
            provider="all",
            retryable=False
        )

    def get_provider_info(self) -> List[Dict]:
        """Return info about all registered providers (available or not)."""
        info = []
        for name, cls in self._registry.items():
            try:
                p = self._get_provider(name)
                if p:
                    info.append({
                        "name": name,
                        "display_name": p.display_name,
                        "available": p.is_available(),
                        "requires_key": p.requires_api_key,
                        "setup_guide": p.get_setup_guide(),
                    })
            except Exception as e:
                info.append({
                    "name": name,
                    "available": False,
                    "error": str(e),
                })
        return info


# Singleton
_router: Optional[ProviderRouter] = None


def get_router(config=None) -> ProviderRouter:
    global _router
    if _router is None:
        _router = ProviderRouter(config=config)
    return _router


if __name__ == "__main__":
    router = get_router()
    print("Available providers:")
    for info in router.get_provider_info():
        status = "✓" if info.get("available") else "✗"
        key = " (needs API key)" if info.get("requires_key") else " (free)"
        print(f"  [{status}] {info.get('display_name', info['name'])}{key}")
