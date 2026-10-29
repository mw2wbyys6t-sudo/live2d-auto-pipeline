#!/usr/bin/env python3
"""
Pygmalion Studio - Pollinations.ai Provider (Free, no API key required)
"""

import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

from core.image_gen.base import (
    ImageProvider,
    GenerationResult,
    GenerationError,
    read_bounded,
)
from core.image_gen.prompt_lang import detect_lang, has_cjk, negative_tags


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
        lang = detect_lang(prompt)
        neg = negative_prompt or negative_tags(lang)

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
                # 此处 URL 由代码常量+编码后的 prompt 构造（非上游返回），SSRF
                # 面可控；允许其 CDN 重定向，但响应体流式读取并限制总体积。
                resp = requests.get(url, params=params, timeout=self.timeout, stream=True)
                resp.raise_for_status()
                content = read_bounded(resp)
                # Check content type is an image
                content_type = resp.headers.get('Content-Type', '')
                if 'image' not in content_type and not content[:8].startswith(b'\x89PNG') and not content[:3] == b'\xff\xd8\xff':
                    # Might have gotten HTML error page
                    raise GenerationError(
                        f"Unexpected content type: {content_type}",
                        provider=self.name,
                        retryable=True
                    )

                self._report_progress("Downloading", 80)
                path = self._save_image(content, output_path)
                self._report_progress("Done", 100)

                return GenerationResult(
                    success=True,
                    image_path=path,
                    provider=self.name,
                    model=model,
                    prompt=optimized_prompt,
                    width=width,
                    height=height,
                    metadata={
                        **self._prompt_metadata(prompt),
                        "negative_sent": neg,
                    },
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

    def _save_image(self, data: bytes, output_path: str) -> str:
        """Save decoded image bytes as a real PNG.

        Pollinations may return JPEG bytes even when the caller requests a
        .png filename; re-encoding through PIL guarantees a valid PNG on disk
        so the layering/Live2D pipeline and browsers read it correctly.
        """
        import io
        from PIL import Image

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.save(p, format="PNG")
        return str(p)

    def _build_live2d_prompt(self, prompt: str) -> str:
        """Build a Live2D-optimized prompt.

        Pollinations 背后是 SD/Flux 系模型，只认英文标签；为了不让中文用户
        面对一串无意义的英文而不知情，调用方会把 ``_prompt_metadata`` 里的
        ``prompt_mismatch`` 一并透出。
        """
        live2d_tags = (
            "anime style, full body, front-facing, simple white background, "
            "clean line art, flat colors, chibi or standard proportions, "
            "clear silhouette, high quality, masterpiece"
        )
        return f"{prompt}, {live2d_tags}"

    @staticmethod
    def _prompt_metadata(prompt: str) -> dict:
        """标记提示词语言与「标签语言不匹配」。"""
        return {"prompt_lang": detect_lang(prompt), "prompt_mismatch": has_cjk(prompt)}

    def get_setup_guide(self) -> str:
        return "Pollinations.ai is free and requires no API key or setup."
