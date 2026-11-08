"""生图引擎选择：provider / model / lora 的透传与边界校验。

为什么要有这组测试：`provider=None` 曾经是硬编码，用户无法指定"用本地免费底模"。
一旦接口能指名，就必须同时保证两件事 ——
1) 选中的引擎/模型真的传到 provider（否则选了等于没选）；
2) 组合不成立时返回 400 而不是静默回退（否则配置错误会被误判成模型质量差）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture()
def srv():
    import api_server

    return api_server


class _CapturedEngine:
    """替身：只记录 WorkflowEngine 的构造参数，不真的跑工作流。"""

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, **kwargs):
        self.calls.append(kwargs)

        class _Engine:
            def run(self, **run_kwargs):
                return {
                    "success": True,
                    "character_image": "",
                    "provider": kwargs.get("provider"),
                }

        return _Engine()


def test_engine_selection_is_forwarded_to_the_workflow(srv, monkeypatch):
    calls = []
    import core.workflow as workflow_mod

    monkeypatch.setattr(workflow_mod, "WorkflowEngine", _CapturedEngine(calls))

    req = srv.GenerateCharacterRequest(
        prompt="1girl", provider="comfyui",
        model="illustriousXL_v01.safetensors", lora="char_style_v1")
    srv._run_generate_character(req)

    assert len(calls) == 1
    assert calls[0]["provider"] == "comfyui"
    assert calls[0]["image_model"] == "illustriousXL_v01.safetensors"
    assert calls[0]["lora"] == "char_style_v1"


def test_empty_selection_falls_back_to_auto(srv, monkeypatch):
    """不指定引擎时必须保持旧行为（provider=None → 按优先级自动选择）。"""
    calls = []
    import core.workflow as workflow_mod

    monkeypatch.setattr(workflow_mod, "WorkflowEngine", _CapturedEngine(calls))
    srv._run_generate_character(srv.GenerateCharacterRequest(prompt="1girl"))

    assert calls[0]["provider"] is None
    assert calls[0]["image_model"] == ""
    assert calls[0]["lora"] == ""


def test_unknown_provider_is_rejected(srv, monkeypatch):
    req = srv.GenerateCharacterRequest(prompt="x", provider="not_a_real_engine")
    with pytest.raises(srv.ClientRequestError) as exc:
        srv._validate_engine_request(req)
    assert "not_a_real_engine" in str(exc.value)


def test_model_without_provider_is_rejected(srv):
    """只给 model 不给 provider 时无法确定谁加载它 —— 不能猜。"""
    req = srv.GenerateCharacterRequest(prompt="x", model="some.safetensors")
    with pytest.raises(srv.ClientRequestError) as exc:
        srv._validate_engine_request(req)
    assert "provider" in str(exc.value)


def test_model_on_cloud_provider_is_rejected(srv):
    """云端 provider 的模型名不由我们决定，静默忽略会让用户以为换了模型。"""
    req = srv.GenerateCharacterRequest(prompt="x", provider="pollinations",
                                       model="gpt-image-1")
    with pytest.raises(srv.ClientRequestError) as exc:
        srv._validate_engine_request(req)
    assert "不支持" in str(exc.value)


def test_local_engine_accepts_model_and_lora(srv, monkeypatch):
    req = srv.GenerateCharacterRequest(
        prompt="x", provider="comfyui", model="a.safetensors", lora="b.safetensors")
    srv._validate_engine_request(req)  # 不抛异常即通过


def test_generation_record_keeps_the_actual_engine(srv, monkeypatch, tmp_path):
    """记录里必须能看出这张图是哪个引擎、哪个模型画的。"""
    import json

    latest = tmp_path / "latest.json"
    monkeypatch.setattr(srv, "LATEST_GEN_FILE", latest)

    srv._record_generation({
        "character_image": "/out/a.png",
        "image_url": "/output/a.png",
        "steps": {"generate": {"provider": "comfyui",
                               "model": "illustriousXL_v01+char_style_v1"}},
    })

    record = json.loads(latest.read_text(encoding="utf-8"))
    assert record["provider"] == "comfyui"
    assert record["model"] == "illustriousXL_v01+char_style_v1"
