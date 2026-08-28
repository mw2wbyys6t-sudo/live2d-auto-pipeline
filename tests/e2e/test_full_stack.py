"""
端到端测试：启动真实的 Go API 服务，模拟前端通过 HTTP 调用全链路。

前置条件：已启动 Go API (port 8080) 和 Next.js (port 3000)
可通过 ./run_all_tests.sh 自动启动和关闭
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

PROJECT_ROOT = Path(__file__).parent.parent.parent
GO_API_URL = os.environ.get("GO_API_URL", "http://localhost:8080")
WEB_URL = os.environ.get("WEB_URL", "http://localhost:3000")


def _check_service(url, name, retries=5):
    """服务可达性检查"""
    for i in range(retries):
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    pytest.skip(f"{name} 服务不可达: {url}")


class TestGoAPIDirect:
    """直接调用 Go API"""

    def test_health(self):
        _check_service(f"{GO_API_URL}/api/health", "Go API")
        r = requests.get(f"{GO_API_URL}/api/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        assert "version" in data.get("data", {})

    def test_info_endpoints(self):
        _check_service(f"{GO_API_URL}/api/info", "Go API")
        r = requests.get(f"{GO_API_URL}/api/info", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        # 验证包含关键端点
        paths = {ep["path"] for ep in data["data"]["endpoints"]}
        for must in [
            "/api/health", "/api/generate", "/api/generate/character",
            "/api/characters", "/api/export/live2d", "/api/psd-plan",
        ]:
            assert must in paths, f"缺少端点: {must}"

    def test_models_list(self):
        r = requests.get(f"{GO_API_URL}/api/models", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0

    def test_expressions_list(self):
        r = requests.get(f"{GO_API_URL}/api/expressions", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_character_crud(self):
        """完整 CRUD 测试：Create → Get → Update → List → Delete"""
        # Create
        create_body = {
            "name": f"E2E测试_{int(time.time())}",
            "prompt": "e2e test character",
            "description": "端到端测试",
        }
        r = requests.post(
            f"{GO_API_URL}/api/characters",
            json=create_body,
            headers={"Content-Type": "application/json", "User-Agent": "e2e-test/1.0"},
            timeout=10,
        )
        assert r.status_code in (200, 201), f"创建失败: {r.status_code} {r.text}"
        created = r.json()
        assert created["success"] is True
        char_id = created["data"]["character_id"]
        assert char_id, "缺少 character_id"

        try:
            # Get
            r = requests.get(f"{GO_API_URL}/api/characters/{char_id}", timeout=5)
            assert r.status_code == 200
            got = r.json()
            assert got["success"] is True
            assert got["data"]["character_id"] == char_id

            # Update
            r = requests.put(
                f"{GO_API_URL}/api/characters/{char_id}",
                json={"name": create_body["name"], "description": "已更新"},
                headers={"Content-Type": "application/json", "User-Agent": "e2e-test/1.0"},
                timeout=5,
            )
            assert r.status_code in (200, 204), f"更新失败: {r.status_code} {r.text}"

            # List
            r = requests.get(f"{GO_API_URL}/api/characters", timeout=5)
            assert r.status_code == 200
            listed = r.json()
            assert listed["success"] is True
            assert any(c["character_id"] == char_id for c in (listed["data"] or []))
        finally:
            # Delete
            r = requests.delete(
                f"{GO_API_URL}/api/characters/{char_id}", timeout=5
            )
            assert r.status_code in (200, 204), f"删除失败: {r.status_code}"

    def test_generate_image_validation(self):
        """生成接口应校验 prompt 必填"""
        r = requests.post(
            f"{GO_API_URL}/api/generate",
            json={"width": 512, "height": 512},  # 缺 prompt
            headers={"Content-Type": "application/json", "User-Agent": "e2e-test/1.0"},
            timeout=5,
        )
        assert r.status_code == 400, f"应返回 400，实际 {r.status_code}"

    def test_create_character_validation(self):
        """创建角色应校验 name 必填"""
        r = requests.post(
            f"{GO_API_URL}/api/characters",
            json={"prompt": "x"},  # 缺 name
            headers={"Content-Type": "application/json", "User-Agent": "e2e-test/1.0"},
            timeout=5,
        )
        assert r.status_code == 400, f"应返回 400，实际 {r.status_code}"

    def test_invalid_json(self):
        """非法 JSON 应被拒绝"""
        r = requests.post(
            f"{GO_API_URL}/api/characters",
            data="{invalid",
            headers={"Content-Type": "application/json", "User-Agent": "e2e-test/1.0"},
            timeout=5,
        )
        assert r.status_code == 400

    def test_content_type_required(self):
        """POST 应要求 Content-Type"""
        r = requests.post(
            f"{GO_API_URL}/api/characters",
            data='{"name":"x"}',
            timeout=5,
        )
        # 没有 Content-Type 应被 400 拒绝
        assert r.status_code == 400, f"无 Content-Type 应 400，实际 {r.status_code}"


class TestWebFrontendProxy:
    """通过 Next.js 代理调用（模拟前端调用链路）"""

    def test_health_via_web(self):
        _check_service(f"{WEB_URL}/api/health", "Web")
        r = requests.get(f"{WEB_URL}/api/health", timeout=5)
        assert r.status_code == 200, f"前端代理 health 失败: {r.status_code}"
        data = r.json()
        assert data.get("success") is True
        # 通过代理应能拿到 Go API 的版本号
        assert "v10" in data.get("data", {}).get("version", "")

    def test_info_via_web(self):
        r = requests.get(f"{WEB_URL}/api/info", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True

    def test_models_via_web(self):
        r = requests.get(f"{WEB_URL}/api/models", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert len(data["data"]) > 0

    def test_character_crud_via_web(self):
        """通过前端代理的完整 CRUD"""
        # Create
        r = requests.post(
            f"{WEB_URL}/api/characters",
            json={"name": f"WebE2E_{int(time.time())}", "prompt": "x"},
            headers={"Content-Type": "application/json", "User-Agent": "e2e-test/1.0"},
            timeout=10,
        )
        assert r.status_code in (200, 201), f"前端代理创建失败: {r.status_code} {r.text[:200]}"
        char_id = r.json()["data"]["character_id"]
        try:
            r = requests.get(f"{WEB_URL}/api/characters/{char_id}", timeout=5)
            assert r.status_code == 200
        finally:
            requests.delete(f"{WEB_URL}/api/characters/{char_id}", timeout=5)

    def test_static_pages(self):
        """Next.js 静态页面能正常加载"""
        for path in ["/", "/generate", "/characters", "/live2d", "/chat", "/export"]:
            r = requests.get(f"{WEB_URL}{path}", timeout=10)
            assert r.status_code == 200, f"页面 {path} 返回 {r.status_code}"


class TestInterfaceConsistency:
    """验证三层接口契约一致性（这是发现漂移的核心测试）"""

    def test_response_envelope_consistent(self):
        """所有 GET 接口都应返回 {success, message, data} 包装"""
        endpoints = [
            f"{GO_API_URL}/api/health",
            f"{GO_API_URL}/api/info",
            f"{GO_API_URL}/api/models",
            f"{GO_API_URL}/api/expressions",
            f"{GO_API_URL}/api/characters",
        ]
        for url in endpoints:
            r = requests.get(url, timeout=5)
            if r.status_code != 200:
                continue
            data = r.json()
            assert "success" in data, f"{url} 缺 success 字段: {list(data.keys())}"
            # data 字段可能为 null/[]/{} 但应存在
            assert "data" in data, f"{url} 缺 data 字段: {list(data.keys())}"

    def test_error_response_format(self):
        """错误响应也应符合 {success: false, error: ...} 格式"""
        r = requests.post(
            f"{GO_API_URL}/api/characters",
            json={},  # 缺 name
            headers={"Content-Type": "application/json", "User-Agent": "e2e-test/1.0"},
            timeout=5,
        )
        assert r.status_code == 400
        body = r.json()
        assert body.get("success") is False
        assert "error" in body, f"错误响应缺 error 字段: {list(body.keys())}"

    def test_snake_case_in_json(self):
        """Go API 字段名应使用 snake_case（前端依赖此约定）"""
        r = requests.get(f"{GO_API_URL}/api/info", timeout=5)
        data = r.json()
        # 检查 endpoints 列表中的字段名
        for ep in data["data"]["endpoints"]:
            for key in ep.keys():
                # 字段名应该是小写/下划线
                if key not in ("method", "path", "desc"):
                    pass  # 只检查 key 是 ascii 小写
                assert key == key.lower(), f"字段名应小写: {key} in {ep}"
