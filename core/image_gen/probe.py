"""真实出图测试：直接调用单个 provider，不经过 workflow。

为什么绕开 workflow：``generateWithCharacter`` 在任意失败时都会回退
本地占位图并返回 success=true，用它做"测试连接"会把 402/缺 Key 也判成
成功。这里只调 provider 本身，失败就是失败。

输出只包含布尔/耗时/尺寸/脱敏后的错误摘要，**绝不回传 Key**。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict

from core.image_gen.base import GenerationError
from core.image_gen.registry import spec_for

_REDACT = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")


def _safe_message(text: str, limit: int = 300) -> str:
    return _REDACT.sub("[REDACTED]", str(text or ""))[:limit]


def probe(provider: str, out_dir: str) -> Dict:
    """对指定 provider 做一次最小尺寸真实出图。"""
    spec = spec_for(provider)
    if spec is None:
        return {"ok": False, "error_code": "unknown_provider",
                "message": f"未知的图像上游: {provider}"}

    from core.image_gen.router import ProviderRouter

    router = ProviderRouter()
    instance = router._get_provider(provider)  # 同包内部入口，避免为测试新增公开 API
    if instance is None:
        return {"ok": False, "error_code": "unavailable",
                "message": "上游初始化失败（依赖缺失或配置不可读）"}

    if spec.requires_key and not instance.is_available():
        return {"ok": False, "error_code": "missing_key",
                "message": "尚未配置 API Key", "probe_note": spec.probe_note}
    if spec.local and not instance.is_available():
        return {"ok": False, "error_code": "offline",
                "message": "本地引擎未启动或地址不可达", "probe_note": spec.probe_note}

    width, height = spec.probe_size
    out_path = Path(out_dir) / f"probe_{provider}_{int(time.time())}.png"
    started = time.monotonic()
    try:
        result = instance.generate(
            prompt="anime girl, full body, front view, simple white background, clean line art",
            output_path=str(out_path),
            width=width,
            height=height,
            negative_prompt="low quality, blurry, extra fingers",
            seed=42,
            # 免费通道有 45 秒/张的硬限速：测试连接不该把界面挂住等满一轮，
            # 等不起就返回 rate_limited，让用户知道「过多久再来测」。
            max_wait=5,
        )
    except GenerationError as exc:
        code = getattr(exc, "code", "") or ""
        if not code and "402" in str(exc):
            code = "http_402"
        return {
            "ok": False,
            "error_code": code or "generation_failed",
            "message": _safe_message(str(exc)),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "probe_size": [width, height],
            "probe_note": spec.probe_note,
        }
    except Exception as exc:  # 网络/依赖异常也按业务失败返回，不抛 500
        return {
            "ok": False,
            "error_code": "failed",
            "message": _safe_message(f"{type(exc).__name__}: {exc}"),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "probe_size": [width, height],
            "probe_note": spec.probe_note,
        }

    return {
        "ok": True,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "probe_size": [width, height],
        "provider": provider,
        "model": getattr(result, "model", ""),
        "image_path": getattr(result, "image_path", str(out_path)),
        "probe_note": spec.probe_note,
    }
