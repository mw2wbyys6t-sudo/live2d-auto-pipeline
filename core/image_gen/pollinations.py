#!/usr/bin/env python3
"""
Live2D Master Agent - Pollinations.ai Provider (Free, no API key required)
"""

import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

from core.image_gen.base import ImageProvider, GenerationResult, GenerationError

LIVE2D_NEGATIVE_PROMPT = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
    "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
    "signature, watermark, username, blurry, deformed, mutated, ugly, disfigured, "
    "3d, realistic, photorealistic, nsfw"
)


class PollinationsProvider(ImageProvider):
    """Free image generation via pollinations.ai - no API key needed."""

    name = "pollinations"
    display_name = "Pollinations.ai (Free)"
    requires_api_key = False
    max_retries = 3
    timeout = 90

    BASE_URL = "https://image.pollinations.ai/prompt/"

    def is_available(self) -> bool:
        return True  # Always available - no API key needed

    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        model: str = "flux",
        **kwargs
    ) -> GenerationResult:
        self._report_progress("Generating (Pollinations.ai)", 10)

        # Build optimized prompt for Live2D character design
        optimized_prompt = self._build_live2d_prompt(prompt)
        neg = negative_prompt or LIVE2D_NEGATIVE_PROMPT

        params = {
            "width": width,
            "height": height,
            "nologo": "true",
            "safe": "false",
            "model": model,
            "negative": neg,
        }
        if seed is not None:
            params["seed"] = seed

        encoded_prompt = urllib.parse.quote(optimized_prompt)
        url = f"{self.BASE_URL}{encoded_prompt}"

        self._report_progress("Generating (Pollinations.ai)", 30)

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout, stream=True)
                resp.raise_for_status()

                # Check content type is an image
                content_type = resp.headers.get('Content-Type', '')
                if 'image' not in content_type and not resp.content[:8].startswith(b'\x89PNG') and not resp.content[:3] == b'\xff\xd8\xff':
                    # Might have gotten HTML error page
                    raise GenerationError(
                        f"Unexpected content type: {content_type}",
                        provider=self.name,
                        retryable=True
                    )

                self._report_progress("Downloading", 80)
                path = self._save_bytes(resp.content, output_path)
                self._report_progress("Done", 100)

                return GenerationResult(
                    success=True,
                    image_path=path,
                    provider=self.name,
                    model=model,
                    prompt=optimized_prompt,
                    width=width,
                    height=height,
                )
            except requests.RequestException as e:
                last_error = str(e)
                time.sleep(2 ** attempt)
            except GenerationError:
                raise

        raise GenerationError(
            f"Pollinations generation failed after {self.max_retries} attempts: {last_error}",
            provider=self.name,
            retryable=False
        )

    def _build_live2d_prompt(self, prompt: str) -> str:
        """Build a Live2D-optimized prompt."""
        live2d_tags = (
            "anime style, full body, front-facing, simple white background, "
            "clean line art, flat colors, chibi or standard proportions, "
            "clear silhouette, high quality, masterpiece"
        )
        return f"{prompt}, {live2d_tags}"

    def get_setup_guide(self) -> str:
        return "Pollinations.ai is free and requires no API key or setup."
