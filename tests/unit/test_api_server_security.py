#!/usr/bin/env python3
"""P1/P2 安全加固的真实行为测试（FastAPI TestClient + 真实线程池）。

覆盖：
1. 线程池任务超时：默认/环境变量覆盖/0=不限/非法值回退四个配置分支；
   _to_thread_bounded 真实等待与超时；/api/generate 超时返回 504 且快速失败。
2. CORS：白名单来源放行且带 credentials；恶意来源无 ACAO 头；预检 OPTIONS 受控；
   通配符 "*" 模式下强制无 credentials（子进程中以环境变量重建 app）。
3. 尺寸钳制：超界宽高经 /api/generate 被钳到 64~4096；<=0 不触碰。
4. 角色 ID 路径穿越：GET/PUT/DELETE 对 ".." 等非法 ID 一律 400；合法但不存在给 404。
5. 导出目录归属：项目根之外、内含 PNG 的目录不得被选为图层来源。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import api_server as srv  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ---------- _env_timeout：四个配置分支 ----------
def test_env_timeout_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("LIVE2D_TEST_TIMEOUT", raising=False)
    assert srv._env_timeout("LIVE2D_TEST_TIMEOUT", 123) == 123


def test_env_timeout_env_override(monkeypatch):
    monkeypatch.setenv("LIVE2D_TEST_TIMEOUT", "42")
    assert srv._env_timeout("LIVE2D_TEST_TIMEOUT", 123) == 42.0


def test_env_timeout_zero_means_unlimited(monkeypatch):
    monkeypatch.setenv("LIVE2D_TEST_TIMEOUT", "0")
    assert srv._env_timeout("LIVE2D_TEST_TIMEOUT", 123) == 0.0


@pytest.mark.parametrize("bad_value", ["abc", "-1", "3.2.1"])
def test_env_timeout_invalid_falls_back_with_warning(monkeypatch, capsys, bad_value):
    monkeypatch.setenv("LIVE2D_TEST_TIMEOUT", bad_value)
    assert srv._env_timeout("LIVE2D_TEST_TIMEOUT", 123) == 123
    assert "回退默认值" in capsys.readouterr().out


# ---------- _to_thread_bounded：真实线程 + 真实计时 ----------
def test_bounded_thread_returns_result():
    assert asyncio.run(srv._to_thread_bounded(lambda x: x * 2, 10, 21)) == 42


def test_bounded_thread_raises_timeout_quickly():
    # 计时必须放在协程内部：asyncio.run 关闭时会回收默认线程池，
    # 等待残留 sleep 线程跑完（Python 不能强杀线程），那部分不属于等待超时。
    async def scenario():
        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await srv._to_thread_bounded(time.sleep, 0.2, 1.0)
        return time.monotonic() - start

    elapsed = asyncio.run(scenario())
    assert elapsed < 0.8, f"超时应在阈值附近触发，实际等了 {elapsed:.2f}s"


def test_bounded_thread_zero_means_unlimited():
    assert asyncio.run(srv._to_thread_bounded(lambda: "done", 0)) == "done"


# ---------- 尺寸钳制 ----------
def test_clamp_dimensions_bounds_and_preserves_non_positive():
    req = SimpleNamespace(width=10000, height=8)
    srv._clamp_dimensions(req)
    assert (req.width, req.height) == (4096, 64)

    req = SimpleNamespace(width=0, height=-5)  # <=0 留给后续默认值逻辑
    srv._clamp_dimensions(req)
    assert (req.width, req.height) == (0, -5)


# ---------- CORS ----------
@pytest.fixture()
def client():
    with TestClient(srv.app) as c:
        yield c


def test_cors_allows_whitelisted_origin_with_credentials(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert r.headers["access-control-allow-credentials"] == "true"


def test_cors_strips_headers_for_unknown_origin(client):
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    # 安全关键是 ACAO 必须缺失：没有它，浏览器绝不会把响应交给跨域 JS。
    # （Starlette 对实际请求可能仍回 ACA-Credentials 单头，无 ACAO 配对即无害。）
    assert "access-control-allow-origin" not in r.headers


def test_cors_preflight_is_origin_controlled(client):
    bad = client.options(
        "/api/health",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in bad.headers

    good = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert good.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert good.headers["access-control-allow-credentials"] == "true"


def test_cors_wildcard_force_disables_credentials():
    """通配符是 CORS + credentials 的致命组合：子进程中以 '*' 重建 app 验证。"""
    code = (
        "import os, sys; os.environ['LIVE2D_CORS_ORIGINS']='*';"
        "sys.path.insert(0, %r);"
        "import api_server as s;"
        "from fastapi.testclient import TestClient;"
        "c = TestClient(s.app);"
        "r = c.get('/api/health', headers={'Origin': 'http://evil.example'});"
        "assert r.headers.get('access-control-allow-origin') == '*', dict(r.headers);"
        "assert 'access-control-allow-credentials' not in r.headers, dict(r.headers);"
        "print('WILDCARD_OK')"
    ) % str(PROJECT_ROOT)
    proc = __import__("subprocess").run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert "WILDCARD_OK" in proc.stdout


# ---------- /api/generate：钳制 + 504 ----------
def test_generate_endpoint_clamps_dimensions(client, monkeypatch):
    seen = {}

    def fake_run(req):
        seen["width"] = req.width
        seen["height"] = req.height
        return {
            "image_path": "", "image_url": "", "provider": "test", "model": "m",
            "prompt": req.prompt, "width": req.width, "height": req.height,
            "seed": 1, "created_at": "t",
        }

    monkeypatch.setattr(srv, "_run_generate", fake_run)
    r = client.post("/api/generate", json={"prompt": "测试", "width": 99999, "height": 8})
    assert r.status_code == 200, r.text
    assert seen == {"width": 4096, "height": 64}


def test_generate_endpoint_timeout_returns_504_fast(client, monkeypatch):
    monkeypatch.setattr(srv, "GEN_TIMEOUT", 0.2)
    monkeypatch.setattr(srv, "_run_generate", lambda req: time.sleep(2))

    start = time.monotonic()
    r = client.post("/api/generate", json={"prompt": "挂起场景"})
    elapsed = time.monotonic() - start

    assert r.status_code == 504
    assert elapsed < 1.5, f"超时请求不该继续挂起，实际耗时 {elapsed:.2f}s"
    body = r.json()
    assert "LIVE2D_GEN_TIMEOUT" in body["error"]
    assert "0 表示不限制" in body["error"]


# ---------- 角色路由：路径穿越 ----------
@pytest.mark.parametrize("bad_id", ["a..b", "evil.json", "a b", "$(id)", "a:b"])
def test_character_routes_reject_illegal_segment_ids(client, bad_id):
    """能作为单段路径到达处理器的非法 ID，必须被 _safe_char_path 白名单拒为 400。"""
    url = "/api/characters/" + quote(bad_id, safe="")
    assert client.get(url).status_code == 400
    assert client.put(url, json={"name": "x"}).status_code == 400
    assert client.delete(url).status_code == 400


@pytest.mark.parametrize("traversal", ["..", "../evil", "a/../../b", "../../etc/passwd"])
def test_character_routes_traversal_cannot_reach_filesystem(client, traversal):
    """含斜杠/点段的穿越 ID 会在路由归一化（404）或白名单（400）任一层被拦下，
    绝不允许 200/500（200 可能命中文件，500 可能已触达文件系统）。"""
    url = "/api/characters/" + quote(traversal, safe="")
    r = client.get(url)
    assert r.status_code in (400, 404), f"穿越 ID 未被拦截: {r.status_code}"


def test_character_route_normal_id_is_not_over_blocked(client):
    # 合法 ID 但记录不存在：必须是 404 而不是 400，证明白名单不误伤
    r = client.get("/api/characters/no_such_id_123")
    assert r.status_code == 404


def test_safe_char_path_unit():
    with pytest.raises(ValueError):
        srv._safe_char_path("../../etc/passwd")
    assert srv._safe_char_path("abc-123_X") == srv.CHARACTERS_DIR / "abc-123_X.json"


# ---------- 导出：外部目录不得作为图层来源 ----------
def test_export_rejects_layers_dir_outside_project_root(client, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(outside / "x.png")

    # 把回落搜索用的 OUTPUT_DIR 指到空目录，保证用例不受工作区残留影响
    empty_out = tmp_path / "empty_output"
    empty_out.mkdir()
    monkeypatch.setattr(srv, "OUTPUT_DIR", empty_out)

    r = client.post("/api/export/live2d", json={"layers_dir": str(outside)})
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["success"] is False
    assert "缺少图层" in body["message"]
    assert str(outside) not in body["message"]


def test_within_project_root_unit(tmp_path):
    assert srv._within_project_root(srv.PROJECT_ROOT / "output" / "a") is True
    assert srv._within_project_root(tmp_path) is False
