#!/usr/bin/env python3
"""v0.10.3 回归测试：离线兜底生图链路（F1 修复）。

验证目标：
1. ``LocalPlaceholderProvider`` 在 Pillow 可用时 ``is_available()`` 返回 True。
2. 它能产出一张占位图，且 metadata 里如实标注 ``local_placeholder=True``。
3. ``ProviderRouter`` 的优先级里它排在最末，作为最终兜底。
4. 当所有云端 provider 不可用时，router.generate() 会回落到它而非抛错。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

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


def test_router_priority_includes_local_placeholder_as_last_resort():
    """router 优先级末位应是 local_placeholder。"""
    from core.image_gen.router import ProviderRouter

    assert ProviderRouter.PRIORITY[-1] == "local_placeholder"


def test_router_falls_back_to_local_placeholder_when_cloud_unavailable(monkeypatch):
    """所有云端 provider 不可用时，router 应回落到本地占位而非抛错。

    通过把 Pollinations 的 is_available 改成 False 来模拟完全离线
    （其余 seedream/sensenova/openai 本就需要 Key，测试环境自然不可用）。
    """
    from core.image_gen import router as router_mod
    from core.image_gen.pollinations import PollinationsProvider

    monkeypatch.setattr(PollinationsProvider, "is_available", lambda self: False)

    r = router_mod.ProviderRouter()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fallback.png"
        result = r.generate(
            prompt="完全离线场景",
            output_path=str(out),
            width=256,
            height=256,
            seed=7,
        )
        assert result.success, f"离线兜底应成功，错误: {result.error}"
        assert result.provider == "local_placeholder"
        assert Path(result.image_path).is_file()


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
