#!/usr/bin/env python3
"""
Live2D Master Agent - Seedream/ARK Provider (DEF-003 IMPLEMENTED)

Volcano Engine ARK / Seedream image generation integration.
Seedream is ByteDance's high-quality image generation model,
accessible via the Volcano Engine ARK API.
"""

import json
import time
import base64
from typing import Optional

import requests

from core.image_gen.base import ImageProvider, GenerationResult, GenerationError


class SeedreamProvider(ImageProvider):
    """Volcano Engine ARK / Seedream image generation (DEF-003).

    Seedream models:
    - seedream-3.0-t2i: Fast text-to-image
    - seedream-4.0: High quality
    - seedream-5.0: Ultra quality (up to 4096x4096)
    """

    name = "seedream"
    display_name = "Seedream/火山引擎 ARK (DEF-003)"
    requires_api_key = True
    max_retries = 3
    timeout = 120

    # Supported sizes per model version
    SIZE_MAP = {
        "seedream-3.0-t2i": ["1024x1024", "1024x768", "768x1024"],
        "seedream-4.0": ["1024x1024", "1024x768", "768x1024", "1536x1024", "1024x1536", "2048x2048"],
        "seedream-5.0": ["1024x1024", "2048x2048", "3072x3072", "4096x4096", "2048x3072", "3072x2048"],
    }

    def __init__(self, config=None):
        super().__init__(config)
        self.model = self.config.seedream_version
        self.size = self.config.seedream_size

    def is_available(self) -> bool:
        return bool(self.config.ark_api_key)

    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        model: Optional[str] = None,
        quality: Optional[str] = None,
        **kwargs
    ) -> GenerationResult:
        """Generate image using Seedream/ARK API.

        Uses the Volcano Engine ARK OpenAI-compatible endpoint for image generation.
        """
        api_key = self.config.ark_api_key
        if not api_key:
            raise GenerationError(
                "ARK/Seedream API key not configured. Set ARK_API_KEY in .env",
                provider=self.name,
                retryable=False
            )

        use_model = model or self.model
        size = self._pick_size(width, height, use_model)
        qual = quality or self.config.get("SEEDREAM_QUALITY", "standard")

        base_url = self.config.ark_base_url.rstrip('/')

        self._report_progress(f"Requesting ({use_model})", 10)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # ARK uses OpenAI-compatible /images/generations endpoint
        payload = {
            "model": use_model,
            "prompt": self._build_prompt(prompt),
            "size": size,
            "n": 1,
            "response_format": "b64_json",
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed
        if qual in ("high", "hd"):
            payload["quality"] = "hd"

        last_error = None
        for attempt in range(self.max_retries):
            try:
                # Initial request
                resp = requests.post(
                    f"{base_url}/images/generations",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )

                if resp.status_code == 401:
                    raise GenerationError(
                        "Invalid ARK API key. Check ARK_API_KEY.",
                        provider=self.name,
                        retryable=False
                    )
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1)
                    self._report_progress(f"Rate limited, waiting {wait}s", 20)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue

                resp.raise_for_status()
                resp_data = resp.json()

                self._report_progress("Processing response", 70)

                # Extract image
                img_data = self._extract_image_data(resp_data)
                if img_data is None:
                    # Might be async - poll for result
                    task_id = self._extract_task_id(resp_data)
                    if task_id:
                        img_data = self._poll_task(base_url, headers, task_id)
                    else:
                        raise GenerationError(
                            f"Unexpected response format: {json.dumps(resp_data)[:300]}",
                            provider=self.name,
                            retryable=attempt < self.max_retries - 1
                        )

                self._report_progress("Saving image", 90)
                path = self._save_bytes(img_data, output_path)
                self._report_progress("Done", 100)

                return GenerationResult(
                    success=True,
                    image_path=path,
                    provider=self.name,
                    model=use_model,
                    prompt=prompt,
                    width=width,
                    height=height,
                    metadata={"quality": qual},
                )

            except requests.RequestException as e:
                last_error = str(e)
                time.sleep(2 ** attempt)

        raise GenerationError(
            f"Seedream/ARK generation failed after {self.max_retries} attempts: {last_error}",
            provider=self.name,
            retryable=False
        )

    def _extract_image_data(self, resp: dict) -> Optional[bytes]:
        """Extract raw image bytes from API response."""
        try:
            if "data" in resp and resp["data"]:
                item = resp["data"][0]
                if "b64_json" in item:
                    return base64.b64decode(item["b64_json"])
                if "url" in item:
                    img_resp = requests.get(item["url"], timeout=60)
                    img_resp.raise_for_status()
                    return img_resp.content
        except Exception:
            return None
        return None

    def _extract_task_id(self, resp: dict) -> Optional[str]:
        """Extract async task ID from response."""
        return resp.get("id") or resp.get("task_id")

    def _poll_task(self, base_url: str, headers: dict, task_id: str, max_wait: int = 120) -> bytes:
        """Poll async task until completion."""
        poll_url = f"{base_url}/images/tasks/{task_id}"
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(3)
            self._report_progress(f"Processing... {int(time.time()-start)}s", 50)
            resp = requests.get(poll_url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "").lower()
            if status in ("succeeded", "completed", "success"):
                img = self._extract_image_data(data)
                if img:
                    return img
            if status in ("failed", "error", "cancelled"):
                raise GenerationError(
                    f"Task {task_id} failed: {data.get('error', {}).get('message', 'unknown')}",
                    provider=self.name,
                    retryable=False
                )
        raise GenerationError(f"Task {task_id} timed out after {max_wait}s", provider=self.name)

    def _pick_size(self, w: int, h: int, model: str) -> str:
        """Pick closest supported size for the model."""
        supported = self.SIZE_MAP.get(model, self.SIZE_MAP["seedream-4.0"])
        # Parse supported sizes
        candidates = []
        for s in supported:
            sw, sh = map(int, s.split('x'))
            candidates.append((abs(sw - w) + abs(sh - h), s))
        candidates.sort()
        return candidates[0][1] if candidates else "1024x1024"

    def _build_prompt(self, prompt: str) -> str:
        """Build optimized prompt for Seedream with Live2D-specific tags."""
        live2d_tags = (
            "anime style, high quality, masterpiece, best quality, "
            "full body, front-facing, T-pose or A-pose, simple white background, "
            "clean lineart, flat shading, distinct color regions, "
            "character design for Live2D rigging"
        )
        return f"{prompt}, {live2d_tags}"

    def get_setup_guide(self) -> str:
        return (
            "Seedream/火山引擎 ARK Setup (DEF-003):\n"
            "1. Visit https://console.volcengine.com/ark\n"
            "2. Enable Seedream model access\n"
            "3. Create an API key\n"
            "4. Run: python config_api.py\n"
            "5. Select ARK/Seedream and enter your API key\n"
            "   Or set ARK_API_KEY in .env file"
        )
