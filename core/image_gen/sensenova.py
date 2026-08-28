#!/usr/bin/env python3
"""
Live2D Master Agent - SenseNova (商汤日日新) Provider
High-quality cloud API provider.
"""

import json
import time
import base64
from pathlib import Path
from typing import Optional

import requests

from core.image_gen.base import ImageProvider, GenerationResult, GenerationError


class SenseNovaProvider(ImageProvider):
    """SenseNova/商汤日日新 image generation API."""

    name = "sensenova"
    display_name = "SenseNova (商汤日日新)"
    requires_api_key = True
    max_retries = 3
    timeout = 120

    # Size mapping for SenseNova API
    SIZE_MAP = {
        (512, 512): "512x512",
        (768, 768): "768x768",
        (1024, 1024): "1024x1024",
        (1024, 768): "1024x768",
        (768, 1024): "768x1024",
        (1024, 1536): "1024x1536",
        (1536, 1024): "1536x1024",
        (2048, 2048): "2048x2048",
    }

    def is_available(self) -> bool:
        return bool(self.config.sensenova_api_key)

    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        model: str = "sensejourney-v2-0",
        **kwargs
    ) -> GenerationResult:
        api_key = self.config.sensenova_api_key
        if not api_key:
            raise GenerationError("SenseNova API key not configured", provider=self.name, retryable=False)

        base_url = self.config.sensenova_base_url.rstrip('/')
        size = self._map_size(width, height)

        self._report_progress("Requesting (SenseNova)", 10)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "prompt": self._optimize_prompt(prompt),
            "size": size,
            "n": 1,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed

        last_error = None
        for attempt in range(self.max_retries):
            try:
                # Submit generation request
                resp = requests.post(
                    f"{base_url}/images/generations",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )

                if resp.status_code == 401:
                    raise GenerationError("Invalid SenseNova API key", provider=self.name, retryable=False)
                if resp.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue

                resp.raise_for_status()
                data = resp.json()

                self._report_progress("Processing (SenseNova)", 60)

                # Extract image data from response
                image_url = None
                image_b64 = None
                if "data" in data and data["data"]:
                    item = data["data"][0]
                    image_url = item.get("url")
                    image_b64 = item.get("b64_json")

                if image_url:
                    self._report_progress("Downloading", 80)
                    img_resp = requests.get(image_url, timeout=60)
                    img_resp.raise_for_status()
                    img_data = img_resp.content
                elif image_b64:
                    img_data = base64.b64decode(image_b64)
                else:
                    raise GenerationError(
                        f"No image in response: {json.dumps(data)[:200]}",
                        provider=self.name,
                        retryable=attempt < self.max_retries - 1
                    )

                path = self._save_bytes(img_data, output_path)
                self._report_progress("Done", 100)

                return GenerationResult(
                    success=True,
                    image_path=path,
                    provider=self.name,
                    model=model,
                    prompt=prompt,
                    width=width,
                    height=height,
                )

            except requests.RequestException as e:
                last_error = str(e)
                time.sleep(2 ** attempt)

        raise GenerationError(
            f"SenseNova generation failed: {last_error}",
            provider=self.name,
            retryable=False
        )

    def _map_size(self, w: int, h: int) -> str:
        """Map dimensions to closest supported size."""
        # Find closest supported size
        closest = min(self.SIZE_MAP.keys(), key=lambda x: abs(x[0] - w) + abs(x[1] - h))
        return self.SIZE_MAP[closest]

    def _optimize_prompt(self, prompt: str) -> str:
        """Add Live2D-specific prompt optimizations."""
        tags = (
            "anime style, high quality, masterpiece, full body, front-facing, "
            "clear outlines, simple background, character design for Live2D rigging"
        )
        return f"{prompt}, {tags}"

    def get_setup_guide(self) -> str:
        return (
            "SenseNova Setup:\n"
            "1. Visit https://platform.sensenova.cn/\n"
            "2. Register and get an API key\n"
            "3. Run: python config_api.py\n"
            "4. Enter your API key when prompted"
        )
