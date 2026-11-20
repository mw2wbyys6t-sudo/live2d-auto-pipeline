#!/usr/bin/env python3
"""
Pygmalion Studio - Pollinations.ai Provider (Free, no API key required)
"""

import os
import re
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
from core.image_gen.prompt_lang import (
    LIVE2D_TAGS,
    adapt_prompt,
    dedupe_tags,
    detect_lang,
    merge_negative,
    negative_tags,
)
from core.logger import get_logger

log = get_logger("image_gen")


class PollinationsProvider(ImageProvider):
    """Free image generation via pollinations.ai - no API key needed."""

    name = "pollinations"
    display_name = "Pollinations.ai (Free)"
    requires_api_key = False
    max_retries = 3
    timeout = 90

    BASE_URL = "https://image.pollinations.ai/prompt/"

    #: 永久性 HTTP 结论 → 该说什么。这类错误重试不会变好，只会让用户多等
    #: 2+4 秒后看到一句读不出原因的「failed after 3 attempts」。
    #: 429 与 5xx 不在这里：那是限流/抖动，该继续按原策略退避。
    _PERMANENT_HTTP_HINTS = {
        402: ("免密通道限流（HTTP 402）：免费配额是每 IP 每 45 秒 1 张，"
              "等待后仍被拒绝。请稍后再试，或在设置里配置 Seedream / SenseNova / "
              "OpenAI 兼容端点。"),
        401: ("免密通道要求鉴权（HTTP 401）：该端点已不再免密可用，"
              "请在设置里填入图像服务密钥后重试。"),
        403: ("免密通道拒绝访问（HTTP 403）：权限或风控限制，"
              "请改用已配置的图像服务，或稍后再试。"),
    }

    #: 免费通道的硬限制（2026-11 实测）：每 IP 每 45 秒 1 张，
    #: 超过就返回 HTTP 402 + {"error":{"code":"FREE_LIMIT"}}。
    #: 以前 402 被当永久失败直接放弃，于是「刚看一眼设置/刚生成过」的第二次
    #: 请求必然失败，Go 侧再回退本地占位图 —— 用户看到的就是"生成了占位图"。
    default_min_interval = 45.0
    #: 单次最多为限流等多久；超过就如实报错，不让用户对着转圈干等。
    max_limit_wait = 90.0
    #: 一次生成里最多为限流让路几次。
    max_limit_waits = 2

    #: 上一次**出图成功**的时刻（类级：同一进程内的连续生成也共享这条限流）。
    #: 只记成功而非每次请求：4xx/5xx 的失败重试不该被拖成每轮 45 秒。
    _last_success_at: Optional[float] = None

    _RETRY_AFTER_RE = re.compile(r"retry after\s+(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)

    def _min_interval(self) -> float:
        """免费通道的最小请求间隔（秒），可用 POLLINATIONS_MIN_INTERVAL 覆盖。"""
        raw = (os.environ.get("POLLINATIONS_MIN_INTERVAL") or "").strip()
        if not raw:
            return self.default_min_interval
        try:
            return max(0.0, float(raw))
        except ValueError:
            return self.default_min_interval

    def _wait_for_slot(self, max_wait: Optional[float] = None) -> None:
        """按免费通道的 45 秒/张限速排队；等不起就立刻给出可照做的错误。

        ``max_wait`` 用于「测试连接」这类不该挂住界面的调用：剩余等待超过它
        就抛 ``rate_limited``，而不是傻等 45 秒。
        """
        interval = self._min_interval()
        last = PollinationsProvider._last_success_at
        if interval <= 0 or last is None:
            return
        remain = interval - (time.monotonic() - last)
        if remain <= 0:
            return
        if max_wait is not None and remain > max_wait:
            raise GenerationError(
                f"免费通道每 {int(interval)} 秒只能出 1 张图，还需等待 "
                f"{int(remain) + 1} 秒；可稍后重试，或在设置里配置其他图像服务。",
                provider=self.name,
                retryable=True,
                code="rate_limited",
            )
        self._report_progress(f"免费通道限流，等待 {int(remain) + 1} 秒", 20)
        time.sleep(remain)

    @classmethod
    def _free_limit_wait(cls, response) -> Optional[float]:
        """解析 402 FREE_LIMIT 要求的等待秒数；不是 FREE_LIMIT 返回 None。"""
        body = ""
        header = ""
        if response is not None:
            try:
                body = response.text or ""
            except Exception:  # noqa: BLE001 - 读不到响应体就当没有
                body = ""
            header = response.headers.get("Retry-After") or ""
        if "FREE_LIMIT" not in body and not header:
            return None
        match = cls._RETRY_AFTER_RE.search(body)
        if match:
            return float(match.group(1))
        try:
            return float(header)
        except (TypeError, ValueError):
            return cls.default_min_interval

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
        max_wait: Optional[float] = None,
        **kwargs
    ) -> GenerationResult:
        self._report_progress("Generating (Pollinations.ai)", 10)

        # Build optimized prompt for Live2D character design. 中文先确定性
        # 适配成英文标签（Flux 读不懂中文，原样发送只会得到随机写实图）。
        adaptation = adapt_prompt(prompt)
        optimized_prompt = self._build_live2d_prompt(adaptation.text)
        neg = merge_negative(negative_prompt, negative_tags("en"))

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
        limit_waits = 0
        skip_pacing = False
        for attempt in range(self.max_retries):
            # 免费通道每 IP 每 45 秒 1 张：同一进程里的连续出图（多候选、
            # 测试连接之后的生成）必须先排队，否则第二次必然 402 → 回退占位图。
            if not skip_pacing:
                self._wait_for_slot(max_wait)
            skip_pacing = False
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
                PollinationsProvider._last_success_at = time.monotonic()
                self._report_progress("Done", 100)

                # 免费通道对匿名请求有像素上限（实测约 0.6MP）：请求
                # 1024×1536 实际只给 627×940。以前这里把「请求尺寸」当成
                # 「实际尺寸」回传，上层据此展示 1024×1536，用户看到的是
                # 一张被放大过的糊图却以为拿到了原尺寸。
                actual_w, actual_h = self._image_size(path)
                downgraded = bool(actual_w and actual_h and (
                    actual_w < width or actual_h < height
                ))
                if downgraded:
                    log.warning(
                        f"免密通道把 {width}×{height} 降到了 {actual_w}×{actual_h}"
                        "（匿名额度上限约 0.6MP）；需要原尺寸请在设置里配置带额度的图像服务"
                    )

                return GenerationResult(
                    success=True,
                    image_path=path,
                    provider=self.name,
                    model=model,
                    prompt=optimized_prompt,
                    width=actual_w or width,
                    height=actual_h or height,
                    metadata={
                        **self._prompt_metadata(prompt),
                        "negative_sent": neg,
                        "limit_waits": limit_waits,
                        "requested_size": [width, height],
                        "actual_size": [actual_w, actual_h],
                        "size_downgraded": downgraded,
                    },
                )
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                # 402 不一定是永久失败：FREE_LIMIT 会告诉我们还要等几秒，
                # 等一下就能拿到图。只有等不起/等过仍被拒才如实失败。
                if status == 402:
                    wait = self._free_limit_wait(response)
                    # 最后一轮不再空等：等完也没有下一次尝试了。
                    if (wait is not None and limit_waits < self.max_limit_waits
                            and attempt + 1 < self.max_retries):
                        if wait > self.max_limit_wait:
                            raise GenerationError(
                                f"免费通道限流：上游要求等待 {int(wait)} 秒才能再出图，"
                                "超过本次可等上限。请稍后重试，或在设置里配置其他图像服务。",
                                provider=self.name,
                                retryable=False,
                                code="http_402",
                            ) from exc
                        limit_waits += 1
                        log.warning(f"Pollinations 免费额度限流，等待 {int(wait) + 1} 秒后重试")
                        self._report_progress(f"免费通道限流，等待 {int(wait) + 1} 秒", 20)
                        time.sleep(wait + 1.0)
                        # 上游已明确"再过 N 秒可以再来"，这段等待已经付过了，
                        # 重试时不要再按 45 秒间隔排一遍。
                        skip_pacing = True
                        continue
                hint = self._PERMANENT_HTTP_HINTS.get(status)
                if hint:
                    raise GenerationError(
                        hint, provider=self.name, retryable=False,
                        code="http_402" if status == 402 else "",
                    ) from exc
                last_error = str(exc)
                time.sleep(2 ** attempt)
            except requests.RequestException as e:
                last_error = str(e)
                time.sleep(2 ** attempt)
            except GenerationError:
                raise

        # 等过限流窗口仍被拒：把「45 秒 1 张」这个事实说清楚，别只说 attempts。
        if limit_waits:
            raise GenerationError(
                self._PERMANENT_HTTP_HINTS[402],
                provider=self.name,
                retryable=False,
                code="http_402",
            )
        raise GenerationError(
            f"Pollinations generation failed after {self.max_retries} attempts: {last_error}",
            provider=self.name,
            retryable=False
        )

    @staticmethod
    def _image_size(path: str) -> tuple[int, int]:
        """返回落盘图片的真实像素尺寸；读不到时返回 (0, 0)。"""
        from PIL import Image

        try:
            with Image.open(path) as img:
                return img.width, img.height
        except Exception:  # noqa: BLE001 - 尺寸探测失败不该让出图失败
            return 0, 0

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
        """拼接 Live2D 英文标签（标签只有一个来源：prompt_lang.LIVE2D_TAGS）。

        中文提示词已在上游 ``adapt_prompt`` 里转成英文标签；这里只负责补
        「全身/正面/白底」等硬约束，保证分层与绑定拿到的是可用的立绘构图。
        """
        tags = LIVE2D_TAGS["en"]
        prompt = (prompt or "").strip()
        if not prompt:
            return tags
        # 适配后的英文标签常与 LIVE2D_TAGS 重复（anime style / full body…）；
        # 去重后再发给上游，避免同一标签被加权两次。
        return dedupe_tags(f"{prompt}, {tags}")

    @staticmethod
    def _prompt_metadata(prompt: str) -> dict:
        """记录提示词语言与「中文适配后仍有多少没被词表覆盖」。"""
        adaptation = adapt_prompt(prompt)
        return {
            "prompt_lang": detect_lang(prompt),
            "prompt_mismatch": adaptation.mismatch,
            "prompt_adapted": adaptation.adapted,
        }

    def get_setup_guide(self) -> str:
        return "Pollinations.ai is free and requires no API key or setup."
