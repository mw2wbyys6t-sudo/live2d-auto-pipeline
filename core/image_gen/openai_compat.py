#!/usr/bin/env python3
"""Pygmalion Studio - OpenAI 兼容图像生成 Provider.

对接任何「OpenAI Images API」兼容的服务端点：
- OpenAI 官方（gpt-image-1 / dall-e-3）
- 国内大模型网关（one-api / new-api 等 OpenAI 兼容中转）
- 云厂商 OpenAI 兼容层（火山方舟、DashScope 兼容模式等）
- 本地自建服务（如 SD WebUI 的 OpenAI 兼容插件、ComfyUI 网关）

配置（.env / 环境变量）：
- OPENAI_API_KEY   必填，密钥
- OPENAI_BASE_URL  选填，默认 https://api.openai.com/v1
- IMAGE_MODEL      选填，默认 gpt-image-1（经网关时可换成任何网关侧模型名）

这是把「外部生成大模型」接进上游路由的标准入口：只要服务兼容
``POST {base_url}/images/generations``，即可被 ProviderRouter 自动发现。

安全边界（防 SSRF，所有出站 URL 一视同仁）：
- 任何进入 requests 的 URL 都先经过 :func:`_CheckedURL.parse`：仅接受
  http(s)，主机解析后的全部 IP 必须通过边界规则，禁止重定向。
- 操作员在 OPENAI_BASE_URL 里配置的主机被视为**显式信任**（本地网关即此类），
  其余地址必须是公网（公网 = ipaddress.is_global，排除私网/环回/链路本地/
  保留段与云元数据地址）。
"""

import base64
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from core.image_gen.base import (
    CheckedURL as _CheckedURL,
    GenerationError,
    GenerationResult,
    ImageProvider,
    checked_image_download,
)
from core.image_gen.prompt_lang import detect_lang, has_cjk
from core.logger import get_logger

log = get_logger("image_gen.openai_compat")

# OpenAI 尺寸档位 → 不强制映射，直接透传最接近的合法值
_SUPPORTED_SIZES = ("1024x1024", "1024x1536", "1536x1024", "512x512", "256x256")


class OpenAICompatProvider(ImageProvider):
    """任意 OpenAI Images API 兼容端点（含中转网关）。"""

    display_name = "OpenAI Compatible"
    requires_api_key = True

    def __init__(self, config=None):
        from core.config import config as global_config
        self.config = config or global_config
        # v0.10.3 修复：统一走 SecureConfig 通道，使 .env 里配置的
        # OPENAI_API_KEY（敏感 Key，存私有 _secrets 而非 os.environ）能被读到。
        # 回退顺序：加密存储 > .env 私有字典 > os.environ（兼容 export 写法）。
        self.api_key = self.config.openai_api_key or ""
        self.base_url = (self.config.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = self.config.image_model or "gpt-image-1"
        # 操作员显式配置的网关主机 = 唯一的非公网信任来源
        gateway_host = (urlparse(self.base_url).hostname or "").lower()
        self._trusted_hosts = frozenset({gateway_host} if gateway_host else ())

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _nearest_size(self, width: int, height: int) -> str:
        requested = f"{width}x{height}"
        if requested in _SUPPORTED_SIZES:
            return requested
        # 立绘多为竖构图：按长宽比就近映射
        if width == height:
            return "1024x1024"
        return "1024x1536" if height > width else "1536x1024"

    def generate(
        self,
        prompt: str,
        output_path: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> GenerationResult:
        if not self.is_available():
            raise GenerationError("OPENAI_API_KEY 未配置，OpenAI Compatible provider 不可用")

        # 拼接负面提示：Images API 无独立字段，走提示词工程约定
        full_prompt = prompt if not negative_prompt else f"{prompt}\n\nAvoid: {negative_prompt}"

        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "n": 1,
            "size": self._nearest_size(width, height),
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        started = time.time()
        # 边界校验：网关地址（操作员显式配置）也必须通过协议/解析检查
        endpoint = _CheckedURL.parse(
            self.base_url + "/images/generations", self._trusted_hosts
        ).url
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=180)
        except requests.RequestException as e:
            raise GenerationError(f"OpenAI 兼容端点请求失败: {e}") from e

        if resp.status_code != 200:
            raise GenerationError(
                f"OpenAI 兼容端点返回 {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json().get("data") or []
        if not data:
            raise GenerationError("OpenAI 兼容端点未返回图像数据")

        item = data[0]
        image_bytes: Optional[bytes] = None
        if item.get("b64_json"):
            try:
                image_bytes = base64.b64decode(item["b64_json"])
            except Exception as e:
                raise GenerationError(f"b64 图像解码失败: {e}") from e
        elif item.get("url"):
            # 端点返回的下载 URL 是不可信输入：统一走共享护栏（公网校验 +
            # 仅信任同网关主机 + 禁止重定向 + 体积上限）。
            image_bytes = checked_image_download(
                item["url"], trusted_hosts=self._trusted_hosts, timeout=120
            )
        if not image_bytes:
            raise GenerationError("响应中既无 b64_json 也无 url")

        out = Path(output_path or self._default_output_path())
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(image_bytes)

        return GenerationResult(
            success=True,
            image_path=str(out),
            width=width,
            height=height,
            seed=seed if seed is not None else 0,
            provider=self.display_name,
            model=self.model,
            prompt=full_prompt,
            elapsed_seconds=round(time.time() - started, 2),
            metadata={
                "prompt_lang": detect_lang(prompt),
                "prompt_mismatch": has_cjk(prompt),
            },
        )

    def _default_output_path(self) -> str:
        out_dir = Path(self.config.output_dir)
        return str(out_dir / f"openai_compat_{int(time.time())}.png")
