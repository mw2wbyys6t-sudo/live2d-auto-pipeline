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
        # 占位图不在默认顺序里：新用户不该拿到一张几何假图
        assert "local_placeholder" not in priority

    def test_local_only_filters_cloud_providers(self, monkeypatch):
        from core.image_gen.router import ProviderRouter

        monkeypatch.setenv("LIVE2D_LOCAL_ONLY", "1")
        priority = ProviderRouter().effective_priority()
        assert priority == ["comfyui", "sd_webui"]
        assert "pollinations" not in priority

    def test_local_only_never_calls_cloud(self, tmp_path, monkeypatch):
        """本地模式下即使云端「可用」也不许被调用；本机引擎都没起来就如实失败。"""
        from core.image_gen import router as router_mod
        from core.image_gen.base import GenerationError
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
        with pytest.raises(GenerationError) as exc:
            router.generate(prompt="离线模式", output_path=str(out),
                            width=256, height=256, seed=3)
        # 失败要能照着做，而不是 "Last error: None"
        assert "设置" in str(exc.value)
        assert not out.exists(), "失败时不该留下一张假图"


# ---------- ComfyUI：LoRA 注入 ----------


def _comfy_routes_with_lora(png: bytes, loras=("char_style_v1.safetensors",)):
    """在基础路由上加 LoraLoader 的可用列表。"""
    routes = _comfy_routes(png)
    routes["GET /object_info/LoraLoader"] = FakeResponse(
        200,
        {"LoraLoader": {"input": {"required": {"lora_name": [list(loras)]}}}},
    )
    return routes


class TestComfyUILoraInjection:
    """LoRA 是角色一致性的落点：必须真的接进采样图，而不是只写在配置里。"""

    def _provider(self, monkeypatch, routes, tmp_path):
        from core.image_gen.comfyui import ComfyUIProvider

        monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
        monkeypatch.delenv("COMFYUI_CHECKPOINT", raising=False)
        monkeypatch.delenv("COMFYUI_WORKFLOW", raising=False)
        session = FakeSession(routes)
        return ComfyUIProvider(session=session), session, tmp_path / "out.png"

    def test_without_lora_the_graph_keeps_its_original_shape(
            self, tmp_path, monkeypatch):
        """未配 LoRA 时结构必须逐键不变 —— 既有产物与断言都依赖这套节点编号。"""
        provider, session, out = self._provider(
            monkeypatch, _comfy_routes_with_lora(_png_bytes()), tmp_path)
        monkeypatch.delenv("COMFYUI_LORA", raising=False)

        provider.generate(prompt="1girl", output_path=str(out), seed=7)

        posted = _find_call(session, "POST /prompt")["json"]["prompt"]
        assert set(posted) == {"3", "4", "5", "6", "7", "8", "9"}
        assert not any(n["class_type"] == "LoraLoader" for n in posted.values())
        assert posted["3"]["inputs"]["model"] == ["4", 0]
        assert posted["6"]["inputs"]["clip"] == ["4", 1]

    def test_lora_node_is_wired_into_sampler_and_both_clips(
            self, tmp_path, monkeypatch):
        provider, session, out = self._provider(
            monkeypatch, _comfy_routes_with_lora(_png_bytes()), tmp_path)
        monkeypatch.setenv("COMFYUI_LORA", "char_style_v1")

        result = provider.generate(prompt="1girl", output_path=str(out), seed=7)

        posted = _find_call(session, "POST /prompt")["json"]["prompt"]
        lora_nodes = [k for k, n in posted.items() if n["class_type"] == "LoraLoader"]
        assert len(lora_nodes) == 1, posted
        lora_id = lora_nodes[0]
        assert posted[lora_id]["inputs"]["lora_name"] == "char_style_v1.safetensors"
        # 模型与 CLIP 都必须经 LoRA 节点，否则只生效一半
        assert posted["3"]["inputs"]["model"] == [lora_id, 0]
        assert posted["6"]["inputs"]["clip"] == [lora_id, 1]
        assert posted["7"]["inputs"]["clip"] == [lora_id, 1]
        # 结果要能复盘到底用了哪个 LoRA
        assert "char_style_v1" in result.model

    def test_lora_strength_is_forwarded_as_float(self, tmp_path, monkeypatch):
        provider, session, out = self._provider(
            monkeypatch, _comfy_routes_with_lora(_png_bytes()), tmp_path)
        monkeypatch.setenv("COMFYUI_LORA", "char_style_v1.safetensors")
        monkeypatch.setenv("COMFYUI_LORA_STRENGTH", "0.75")
        monkeypatch.setenv("COMFYUI_LORA_STRENGTH_CLIP", "0.6")

        provider.generate(prompt="1girl", output_path=str(out), seed=7)

        posted = _find_call(session, "POST /prompt")["json"]["prompt"]
        lora_id = next(k for k, n in posted.items() if n["class_type"] == "LoraLoader")
        assert posted[lora_id]["inputs"]["strength_model"] == pytest.approx(0.75)
        assert posted[lora_id]["inputs"]["strength_clip"] == pytest.approx(0.6)

    def test_illegal_strength_falls_back_to_default(self, tmp_path, monkeypatch):
        provider, session, out = self._provider(
            monkeypatch, _comfy_routes_with_lora(_png_bytes()), tmp_path)
        monkeypatch.setenv("COMFYUI_LORA", "char_style_v1")
        monkeypatch.setenv("COMFYUI_LORA_STRENGTH", "not-a-number")

        provider.generate(prompt="1girl", output_path=str(out), seed=7)

        posted = _find_call(session, "POST /prompt")["json"]["prompt"]
        lora_id = next(k for k, n in posted.items() if n["class_type"] == "LoraLoader")
        assert posted[lora_id]["inputs"]["strength_model"] == pytest.approx(1.0)

    def test_missing_lora_raises_instead_of_silently_ignoring(
            self, tmp_path, monkeypatch):
        """配了却找不到时必须报错：静默忽略会让「以为加了 LoRA」的产物无法解释。"""
        from core.image_gen.base import GenerationError

        routes = _comfy_routes_with_lora(_png_bytes(), loras=("other.safetensors",))
        provider, session, out = self._provider(monkeypatch, routes, tmp_path)
        monkeypatch.setenv("COMFYUI_LORA", "char_style_v1")

        with pytest.raises(GenerationError) as exc:
            provider.generate(prompt="1girl", output_path=str(out), seed=7)

        message = str(exc.value)
        assert "char_style_v1" in message
        assert "other.safetensors" in message, "错误里要列出可用的 LoRA"
        assert not any(k == "POST /prompt" for k, _ in session.calls), \
            "校验失败就不该把采样请求发出去"

    def test_custom_workflow_supports_lora_placeholders(self, tmp_path, monkeypatch):
        from core.image_gen.comfyui import ComfyUIProvider

        workflow = {
            "1": {"class_type": "KSampler", "inputs": {"seed": "{{SEED}}"}},
            "10": {"class_type": "LoraLoader", "inputs": {
                "lora_name": "{{LORA}}",
                "strength_model": "{{LORA_STRENGTH}}",
                "strength_clip": "{{LORA_STRENGTH_CLIP}}",
            }},
        }
        wf_path = tmp_path / "wf_lora.json"
        wf_path.write_text(json.dumps(workflow), encoding="utf-8")

        # workflow_path 在 __init__ 里读取，必须先设 env 再造 provider
        monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
        monkeypatch.delenv("COMFYUI_CHECKPOINT", raising=False)
        monkeypatch.setenv("COMFYUI_WORKFLOW", str(wf_path))
        monkeypatch.setenv("COMFYUI_LORA", "char_style_v1.safetensors")
        monkeypatch.setenv("COMFYUI_LORA_STRENGTH", "0.8")
        monkeypatch.delenv("COMFYUI_LORA_STRENGTH_CLIP", raising=False)

        session = FakeSession(_comfy_routes_with_lora(_png_bytes()))
        provider = ComfyUIProvider(session=session)
        out = tmp_path / "out.png"

        provider.generate(prompt="1girl", output_path=str(out), seed=7)

        posted = _find_call(session, "POST /prompt")["json"]["prompt"]
        assert posted["10"]["inputs"]["lora_name"] == "char_style_v1.safetensors"
        assert posted["10"]["inputs"]["strength_model"] == pytest.approx(0.8)
        assert posted["10"]["inputs"]["strength_clip"] == pytest.approx(0.8)
