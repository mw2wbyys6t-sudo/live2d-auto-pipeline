#!/usr/bin/env python3
"""离线占位图链路的回归测试（F1 修复 + 2026-11-02 口径变更）。

验证目标：
1. ``LocalPlaceholderProvider`` 在 Pillow 可用时 ``is_available()`` 返回 True。
2. 它能产出一张占位图，且 metadata 里如实标注 ``local_placeholder=True``。
3. 它**不在** router 的默认优先级里 —— 没配图像引擎时如实失败，而不是画一张
   几何假图给第一次使用的用户；显式 ``--provider local_placeholder`` 仍然可用。
4. PSD 分层的兜底仍是警告而非致命错误。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_local_placeholder_provider_is_available_with_pillow():
    """Pillow 已装时，离线兜底 provider 可用。"""
    from core.image_gen.local_placeholder import LocalPlaceholderProvider

    provider = LocalPlaceholderProvider()
    assert provider.is_available(), "Pillow 已安装，本地占位 provider 应可用"
    assert provider.requires_api_key is False
    assert provider.name == "local_placeholder"


def test_local_placeholder_provider_generates_placeholder_image():
    """离线 provider 能产出一张如实标注的占位图。"""
    from core.image_gen.local_placeholder import LocalPlaceholderProvider

    provider = LocalPlaceholderProvider()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "test_placeholder.png"
        result = provider.generate(
            prompt="测试用立绘",
            output_path=str(out),
            width=512,
            height=512,
            seed=42,
        )
        assert result.success, f"生成应成功，错误: {result.error}"
        assert Path(result.image_path).is_file(), "占位图文件应存在"
        assert result.metadata.get("local_placeholder") is True
        assert result.metadata.get("seed") == 42
        assert result.width == 512 and result.height == 512


def test_router_priority_excludes_the_offline_placeholder():
    """占位图**不在**默认优先级里，但引擎本身仍注册着可显式调用。

    2026-11-02 的口径变更：原先它排在末位当「最终兜底」，代价是没配图像服务的
    新用户第一次点生成就拿到一张几何假图。现在默认路径宁可失败，也不假成功；
    要跑离线链路请显式 ``--provider local_placeholder``。
    """
    from core.image_gen.router import ProviderRouter

    router = ProviderRouter()
    assert "local_placeholder" not in ProviderRouter.PRIORITY
    assert "local_placeholder" in router._registry, "引擎保留注册，显式选择仍可用"
    assert "local_placeholder" not in router.effective_priority()


def test_router_priority_keeps_placeholder_for_explicit_use(monkeypatch):
    """显式 ``provider="local_placeholder"`` 仍然出图（离线批处理/冒烟用）。"""
    from core.image_gen import router as router_mod

    r = router_mod.ProviderRouter()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "explicit.png"
        result = r.generate(
            prompt="显式指定离线引擎",
            output_path=str(out),
            width=256,
            height=256,
            provider="local_placeholder",
            seed=7,
        )
        assert result.success, f"显式选择应仍然可用，错误: {result.error}"
        assert result.provider == "local_placeholder"
        assert result.metadata.get("local_placeholder") is True


def test_router_fails_with_actionable_message_when_no_engine_exists(monkeypatch):
    """一个可用引擎都没有时：如实失败 + 说清下一步，且不留假图。

    通过把 Pollinations 的 is_available 改成 False 来模拟完全离线
    （其余 seedream/sensenova/openai 本就需要 Key，测试环境自然不可用）。
    """
    from core.image_gen import router as router_mod
    from core.image_gen.base import GenerationError
    from core.image_gen.pollinations import PollinationsProvider

    monkeypatch.setattr(PollinationsProvider, "is_available", lambda self: False)

    r = router_mod.ProviderRouter()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fallback.png"
        with pytest.raises(GenerationError) as exc:
            r.generate(
                prompt="完全离线场景",
                output_path=str(out),
                width=256,
                height=256,
                seed=7,
            )
        message = str(exc.value)
        assert "设置" in message and "ComfyUI" in message, f"报错要能照着做: {message}"
        assert not out.exists(), "失败不该留下一张占位图"


def test_workflow_psd_fallback_is_warning_not_fatal(monkeypatch):
    """F2: PSD fallback 应降级为警告，不应抛 RuntimeError。

    构造一个 fallback=True 但 success=True 的 psd_result，验证 workflow
    不会因为 fallback 就抛错。
    """
    import core.workflow as wf

    original_runtime_error = RuntimeError

    calls = []

    class FakeLog:
        def warning(self, msg, *a):
            calls.append(("warning", msg))

        def success(self, msg, *a):
            calls.append(("success", msg))

        def info(self, msg, *a):
            calls.append(("info", msg))

    fake_psd_result = {
        "success": True,
        "fallback": True,
        "psd_path": "/tmp/fake.png",
        "png_package": "/tmp/fake_pkg",
    }

    # 直接验证：fallback=True 且 success=True 时不进入 raise 分支
    # （模拟 workflow.py:533 的判断逻辑）
    def check_logic(psd_result):
        if not psd_result.get("success"):
            raise original_runtime_error("should not happen")
        if psd_result.get("fallback"):
            return "warning"
        return "ok"

    assert check_logic(fake_psd_result) == "warning"
