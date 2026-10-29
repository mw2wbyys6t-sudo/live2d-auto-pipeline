#!/usr/bin/env python3
"""Stable Diffusion WebUI (A1111 / Forge / SD.Next) 本地生图 Provider。

走 WebUI 自带的 ``/sdapi/v1/txt2img`` 接口，返回 base64 PNG，落盘后交给
后续分层/绑定链路。与 ComfyUI provider 一样只允许连本机。
"""

from __future__ import annotations

import base64
import os
import random
import time
from typing import Optional

import requests

from core.image_gen.base import (
    GenerationError,
    GenerationResult,
    ImageProvider,
    MAX_IMAGE_DOWNLOAD_BYTES,
)
from core.image_gen.local_http import ensure_local_endpoint
from core.logger import get_logger

log = get_logger("image_gen.sd_webui")

DEFAULT_NEGATIVE_PROMPT = (
    "lowres, bad anatomy, bad hands, text, watermark, signature, "
    "blurry, jpeg artifacts, worst quality, low quality"
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


class SDWebUIProvider(ImageProvider):
    """本地 SD WebUI 生图引擎。"""

    name = "sd_webui"
    display_name = "SD WebUI（本地）"
    requires_api_key = False
    is_local = True
    max_retries = 0
    timeout = 600

    def __init__(self, config=None, session: Optional[requests.Session] = None):
        super().__init__(config)
        self.base_url = ensure_local_endpoint(
            os.environ.get("SD_WEBUI_BASE_URL") or "http://127.0.0.1:7860"
        )
        self._session = session
        self._probe_cache: tuple[float, bool] = (0.0, False)

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def is_available(self, force: bool = False) -> bool:
        cached_at, cached = self._probe_cache
        if not force and cached_at and time.monotonic() - cached_at < 3.0:
            return cached
        available = False
        # 启动 WebUI 时必须带 --api，否则这些端点不存在
        for probe in ("/sdapi/v1/options", "/internal/ping"):
            try:
                resp = self.session.get(self._url(probe), timeout=1.5)
                if resp.status_code == 200:
                    available = True
                    break
            except Exception:
                continue
        self._probe_cache = (time.monotonic(), available)
        return available

    def get_setup_guide(self) -> str:
        return (
            "启动 SD WebUI 时加 --api 参数（默认地址 http://127.0.0.1:7860），"
            "例如：webui-user.bat 里 COMMANDLINE_ARGS=--api。\n"
            f"当前地址：{self.base_url}"
        )

    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> GenerationResult:
        if seed is None:
            seed = random.randint(0, 2**31 - 1)

        if not self.is_available(force=True):
            raise GenerationError(
                f"SD WebUI 未运行或未开启 API（{self.base_url}）。"
                "请用 --api 启动 WebUI，或设置 SD_WEBUI_BASE_URL。",
                provider=self.name,
                retryable=False,
            )

        payload = {
            "prompt": prompt or "",
            "negative_prompt": negative_prompt or DEFAULT_NEGATIVE_PROMPT,
            "width": int(width),
            "height": int(height),
            "steps": _env_int("SD_WEBUI_STEPS", 28),
            "cfg_scale": _env_float("SD_WEBUI_CFG", 7.0),
            "sampler_name": os.environ.get("SD_WEBUI_SAMPLER", "DPM++ 2M"),
            "seed": int(seed),
            "batch_size": 1,
            "n_iter": 1,
            "restore_faces": False,
        }

        self._report_progress("提交到 SD WebUI", 20)
        try:
            resp = self.session.post(
                self._url("/sdapi/v1/txt2img"), json=payload, timeout=self.timeout
            )
        except requests.RequestException as e:
            raise GenerationError(f"请求 SD WebUI 失败: {e}", provider=self.name) from e

        if resp.status_code != 200:
            raise GenerationError(
                f"SD WebUI 返回 HTTP {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise GenerationError(
                f"SD WebUI 返回了非 JSON 响应: {resp.text[:200]}", provider=self.name
            ) from e

        images = data.get("images") or []
        if not images:
            raise GenerationError("SD WebUI 没有返回任何图片", provider=self.name)

        raw = str(images[0])
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw, validate=True)
        except Exception as e:
            raise GenerationError(f"解码 SD WebUI 返回的图片失败: {e}",
                                  provider=self.name) from e
        if len(blob) > MAX_IMAGE_DOWNLOAD_BYTES:
            raise GenerationError("SD WebUI 返回图片超过体积上限", provider=self.name)

        self._report_progress("保存结果", 90)
        saved = self._save_bytes(blob, output_path)
        self._report_progress("完成", 100)
        log.success(f"SD WebUI 本地生成完成: {saved}")

        model = ""
        info = data.get("info")
        if isinstance(info, str):
            try:
                import json as _json
                model = str((_json.loads(info) or {}).get("sd_model_name", ""))
            except Exception:
                model = ""
        elif isinstance(info, dict):
            model = str(info.get("sd_model_name", ""))

        return GenerationResult(
            success=True,
            image_path=saved,
            provider=self.name,
            model=model,
            prompt=prompt or "",
            width=int(width),
            height=int(height),
            metadata={"local": True, "engine": "sd_webui", "seed": seed},
        )


if __name__ == "__main__":  # pragma: no cover - 手动自检入口
    p = SDWebUIProvider()
    print(f"SD WebUI: {p.base_url}")
    print(f"可用: {p.is_available(force=True)}")
