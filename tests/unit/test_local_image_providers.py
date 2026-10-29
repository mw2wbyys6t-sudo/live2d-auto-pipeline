#!/usr/bin/env python3
"""本地生图 provider 回归测试（ComfyUI / SD WebUI / 本地模式）。

全部用假的 HTTP session，不依赖真实引擎、不发网络请求。
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------- 测试替身 ----------


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, content: bytes = b""):
        self.status_code = status_code
        self._payload = payload
        self._content = content
        self.text = "" if payload is None else json.dumps(payload, ensure_ascii=False)

    def json(self):
        if self._payload is None:
            raise ValueError("响应不是 JSON")
        return self._payload

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]

    def close(self):
        pass


class FakeSession:
    """按 "METHOD /path" 路由返回预置响应。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _dispatch(self, method: str, url: str, **kwargs):
        path = urlsplit(url).path
        key = f"{method} {path}"
        self.calls.append((key, kwargs))
        route = self.routes.get(key)
        if route is None:
            raise requests.ConnectionError(f"没有为 {key} 配置响应")
        return route() if callable(route) else route

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, **kwargs)


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _find_call(session: FakeSession, key: str):
    for call_key, kwargs in session.calls:
        if call_key == key:
            return kwargs
    raise AssertionError(f"没有发出 {key} 请求，实际: {[c[0] for c in session.calls]}")


# ---------- 地址边界 ----------


class TestLocalEndpointGuard:
    def test_loopback_is_allowed(self):
        from core.image_gen.local_http import ensure_local_endpoint

        assert ensure_local_endpoint("http://127.0.0.1:8188") == "http://127.0.0.1:8188"
        assert ensure_local_endpoint("http://localhost:7860/") == "http://localhost:7860"

    def test_public_address_is_rejected(self):
        from core.image_gen.base import GenerationError
        from core.image_gen.local_http import ensure_local_endpoint

        with pytest.raises(GenerationError):
            ensure_local_endpoint("https://8.8.8.8:8188")

    def test_lan_requires_opt_in(self, monkeypatch):
        from core.image_gen.local_http import ensure_local_endpoint

        monkeypatch.delenv("LIVE2D_LOCAL_ALLOW_LAN", raising=False)
        with pytest.raises(Exception):
            ensure_local_endpoint("http://192.168.1.10:8188")

        monkeypatch.setenv("LIVE2D_LOCAL_ALLOW_LAN", "1")
        assert ensure_local_endpoint("http://192.168.1.10:8188") == "http://192.168.1.10:8188"


# ---------- ComfyUI ----------


def _comfy_routes(png: bytes):
    return {
        "GET /system_stats": FakeResponse(200, {"system": {"os": "nt"}}),
        "GET /object_info/CheckpointLoaderSimple": FakeResponse(
            200,
            {"CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["dreamshaper_8.safetensors"]]}}
            }},
        ),
        "POST /prompt": FakeResponse(200, {"prompt_id": "pid-1"}),
        "GET /history/pid-1": FakeResponse(
            200,
            {"pid-1": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [
                    {"filename": "live2d_00001_.png", "subfolder": "", "type": "output"}
                ]}},
            }},
        ),
        "GET /view": FakeResponse(200, content=png),
    }


class TestComfyUIProvider:
    def test_availability_probe(self, monkeypatch):
        from core.image_gen.comfyui import ComfyUIProvider

        monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
        ok = ComfyUIProvider(session=FakeSession({"GET /system_stats": FakeResponse(200, {})}))
        assert ok.is_available(force=True) is True

        down = ComfyUIProvider(session=FakeSession({}))
        assert down.is_available(force=True) is False

    def test_generate_end_to_end(self, tmp_path, monkeypatch):
        from core.image_gen.comfyui import ComfyUIProvider

        monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
        monkeypatch.delenv("COMFYUI_CHECKPOINT", raising=False)
        monkeypatch.delenv("COMFYUI_WORKFLOW", raising=False)

        png = _png_bytes()
        session = FakeSession(_comfy_routes(png))
        provider = ComfyUIProvider(session=session)
        out = tmp_path / "comfy.png"

        result = provider.generate(
            prompt="1girl, white dress, silver hair",
            output_path=str(out),
            width=512,
            height=768,
            seed=12345,
        )

        assert result.success, result.error
        assert out.is_file() and out.read_bytes() == png
        assert result.provider == "comfyui"
        assert result.model == "dreamshaper_8.safetensors"
        assert result.metadata["engine"] == "comfyui"
        assert result.metadata["seed"] == 12345

        posted = _find_call(session, "POST /prompt")["json"]["prompt"]
        assert posted["4"]["inputs"]["ckpt_name"] == "dreamshaper_8.safetensors"
        assert posted["6"]["inputs"]["text"] == "1girl, white dress, silver hair"
        assert posted["5"]["inputs"]["width"] == 512
        assert posted["5"]["inputs"]["height"] == 768
        assert posted["3"]["inputs"]["seed"] == 12345

    def test_missing_checkpoint_reports_actionable_error(self, tmp_path, monkeypatch):
        from core.image_gen.base import GenerationError
        from core.image_gen.comfyui import ComfyUIProvider

        monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
        monkeypatch.delenv("COMFYUI_CHECKPOINT", raising=False)

        routes = _comfy_routes(_png_bytes())
        routes["GET /object_info/CheckpointLoaderSimple"] = FakeResponse(
            200, {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [[]]}}}}
        )
        provider = ComfyUIProvider(session=FakeSession(routes))

        with pytest.raises(GenerationError) as exc:
            provider.generate(prompt="x", output_path=str(tmp_path / "x.png"))
        assert "checkpoint" in str(exc.value).lower()

    def test_custom_workflow_placeholders(self, tmp_path, monkeypatch):
        from core.image_gen.comfyui import ComfyUIProvider

        monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
        monkeypatch.delenv("COMFYUI_CHECKPOINT", raising=False)

        workflow = {
            "1": {"class_type": "KSampler", "inputs": {
                "seed": "{{SEED}}", "steps": 20, "cfg": 6.5,
                "positive": ["2", 0], "width": "{{WIDTH}}",
            }},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{PROMPT}}"}},
        }
        wf_path = tmp_path / "wf_api.json"
        wf_path.write_text(json.dumps(workflow), encoding="utf-8")
        monkeypatch.setenv("COMFYUI_WORKFLOW", str(wf_path))

        png = _png_bytes()
        routes = _comfy_routes(png)
        routes["GET /object_info/CheckpointLoaderSimple"] = FakeResponse(
            200, {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [[]]}}}}
        )
        session = FakeSession(routes)
        provider = ComfyUIProvider(session=session)
        provider.generate(prompt="银发少女", output_path=str(tmp_path / "wf.png"),
                          width=640, height=960, seed=7)

        posted = _find_call(session, "POST /prompt")["json"]["prompt"]
        assert posted["1"]["inputs"]["seed"] == 7          # 数值占位符保持 int
        assert posted["1"]["inputs"]["width"] == 640
        assert posted["2"]["inputs"]["text"] == "银发少女"

    def test_ui_format_workflow_is_rejected(self, tmp_path, monkeypatch):
        from core.image_gen.base import GenerationError
        from core.image_gen.comfyui import ComfyUIProvider

        monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
        bad = tmp_path / "ui_workflow.json"
        bad.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
        monkeypatch.setenv("COMFYUI_WORKFLOW", str(bad))

        provider = ComfyUIProvider(session=FakeSession(_comfy_routes(_png_bytes())))
        with pytest.raises(GenerationError) as exc:
            provider.generate(prompt="x", output_path=str(tmp_path / "x.png"))
        assert "API" in str(exc.value)


# ---------- SD WebUI ----------


class TestSDWebUIProvider:
    def test_generate_end_to_end(self, tmp_path, monkeypatch):
        from core.image_gen.sd_webui import SDWebUIProvider

        monkeypatch.setenv("SD_WEBUI_BASE_URL", "http://127.0.0.1:7860")
        png = _png_bytes()
        session = FakeSession({
            "GET /sdapi/v1/options": FakeResponse(200, {}),
            "POST /sdapi/v1/txt2img": FakeResponse(200, {
                "images": [base64.b64encode(png).decode("ascii")],
                "info": json.dumps({"sd_model_name": "animePastelDream"}),
            }),
        })
        provider = SDWebUIProvider(session=session)
        out = tmp_path / "sd.png"

        result = provider.generate(prompt="1girl, kimono", output_path=str(out),
                                   width=512, height=512, seed=99)

        assert result.success, result.error
        assert out.read_bytes() == png
        assert result.provider == "sd_webui"
        assert result.model == "animePastelDream"

        payload = _find_call(session, "POST /sdapi/v1/txt2img")["json"]
        assert payload["prompt"] == "1girl, kimono"
        assert payload["seed"] == 99

    def test_unavailable_when_api_disabled(self, monkeypatch):
        from core.image_gen.base import GenerationError
        from core.image_gen.sd_webui import SDWebUIProvider

        monkeypatch.setenv("SD_WEBUI_BASE_URL", "http://127.0.0.1:7860")
        provider = SDWebUIProvider(session=FakeSession({}))
        with pytest.raises(GenerationError):
            provider.generate(prompt="x", output_path="unused.png")


# ---------- Router 接入 ----------


class TestRouterLocalMode:
    def test_local_engines_come_first(self):
        from core.image_gen.router import ProviderRouter

        priority = ProviderRouter().effective_priority()
        assert priority[0] == "comfyui"
        assert priority[1] == "sd_webui"
        assert priority[-1] == "local_placeholder"

    def test_local_only_filters_cloud_providers(self, monkeypatch):
        from core.image_gen.router import ProviderRouter

        monkeypatch.setenv("LIVE2D_LOCAL_ONLY", "1")
        priority = ProviderRouter().effective_priority()
        assert priority == ["comfyui", "sd_webui", "local_placeholder"]
        assert "pollinations" not in priority

    def test_local_only_never_calls_cloud(self, tmp_path, monkeypatch):
        from core.image_gen import router as router_mod
        from core.image_gen.pollinations import PollinationsProvider

        monkeypatch.setenv("LIVE2D_LOCAL_ONLY", "1")
        monkeypatch.setattr(PollinationsProvider, "is_available", lambda self: True)
        monkeypatch.setattr(
            PollinationsProvider, "generate",
            lambda self, **kw: pytest.fail("本地模式下不应调用云端 provider"),
        )
        monkeypatch.setattr("core.image_gen.comfyui.ComfyUIProvider.is_available",
                            lambda self, force=False: False)
        monkeypatch.setattr("core.image_gen.sd_webui.SDWebUIProvider.is_available",
                            lambda self, force=False: False)

        router = router_mod.ProviderRouter()
        out = tmp_path / "offline.png"
        result = router.generate(prompt="离线模式", output_path=str(out),
                                 width=256, height=256, seed=3)
        assert result.success
        assert result.provider == "local_placeholder"
