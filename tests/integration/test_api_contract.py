"""
HTTP 接口契约测试 - 验证 FastAPI 端点（api_server.py）的响应结构与安全边界。

覆盖三类回归风险：
1. 端点内部调用签名漂移（构造器/方法参数不匹配导致端点永久失败）；
2. 响应契约不统一（成功 / 未实现 / 不存在应返回不同且可判定的结构）；
3. 静态文件服务的路径穿越防护。

这些缺陷在编译期不可见，只在运行时暴露，因此必须由契约测试锁定。
"""

import sys
from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import api_server  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    with TestClient(api_server.app) as c:
        yield c


@pytest.fixture
def isolated_output(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(api_server, "OUTPUT_DIR", out)
    return out


@pytest.fixture
def layer_dir(tmp_path):
    layers = tmp_path / "layers_test"
    layers.mkdir()
    colors = {
        "face_base": (255, 220, 180, 255),
        "hair_back": (100, 100, 255, 255),
        "clothes": (50, 150, 50, 255),
    }
    for name, color in colors.items():
        Image.new("RGBA", (128, 128), color).save(layers / f"{name}.png")
    return layers


class TestResponseContract:
    """基础端点的响应结构契约"""

    def test_health_returns_ok_wrapper(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert "data" in body

    def test_missing_character_returns_404_envelope(self, client):
        r = client.get("/api/characters/__definitely_not_exists__")
        assert r.status_code == 404
        body = r.json()
        assert body["success"] is False
        assert isinstance(body["error"], str) and body["error"]
        assert body["data"] is None

    def test_characters_list_is_array(self, client):
        r = client.get("/api/characters")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)


class TestUnimplementedEndpointsAreHonest:
    """未实现的端点必须如实报告失败，不得返回假成功"""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/see-through"),
            ("post", "/api/export/psd"),
            ("get", "/api/export/package"),
        ],
    )
    def test_returns_501_and_success_false(self, client, method, path):
        r = getattr(client, method)(path)
        assert r.status_code == 501, f"{path} 应返回 501，实际 {r.status_code}"
        body = r.json()
        assert body["success"] is False
        assert isinstance(body["error"], str) and body["error"]

    def test_psd_plan_marks_itself_as_template(self, client):
        r = client.post("/api/psd-plan")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["kind"] == "template"
        assert data["applied"] is False

    def test_deploy_desktop_produces_real_package(self, client, tmp_path, monkeypatch):
        """桌宠部署端点已接线到真实 animator，不得返回 501。"""
        from PIL import Image

        layers = tmp_path / "layers"
        layers.mkdir()
        for name in ("hair_back", "hair_front", "face"):
            Image.new("RGBA", (64, 64), (120, 80, 200, 255)).save(layers / f"{name}.png")
        monkeypatch.setattr(api_server, "OUTPUT_DIR", tmp_path / "out")

        r = client.post("/api/deploy/desktop", json={"layers_dir": str(layers)})
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["data"]["package_dir"]
        assert Path(body["data"]["run_script"]).is_file()

    def test_deploy_desktop_without_layers_is_clear_error(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(api_server, "LATEST_GEN_FILE", tmp_path / "missing.json")
        r = client.post("/api/deploy/desktop", json={})
        assert r.status_code == 400
        assert r.json()["success"] is False


class TestStaticFileGuard:
    """静态文件服务不得越出白名单目录"""

    @pytest.mark.parametrize(
        "path",
        [
            "/output/..%2F..%2F.env",
            "/output/%2e%2e%2f%2e%2e%2f.env",
            "/api/preview/..%2F..%2F.env",
            "/api/preview/%2e%2e%2f%2e%2e%2f.env",
        ],
    )
    def test_traversal_is_rejected(self, client, path):
        r = client.get(path)
        assert r.status_code != 200, f"{path} 不应可访问"
        assert r.status_code in (400, 403, 404)

    def test_existing_output_file_is_served(self, client, isolated_output):
        target = isolated_output / "ok.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(target)
        r = client.get("/output/ok.png")
        assert r.status_code == 200
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_missing_output_file_returns_404(self, client, isolated_output):
        r = client.get("/output/not_there.png")
        assert r.status_code == 404


class TestExportLive2DContract:
    """导出端点的调用签名与返回契约（曾因构造参数不匹配而永久失败）"""

    def test_builds_model_from_layers(self, client, isolated_output, layer_dir):
        r = client.post(
            "/api/export/live2d",
            json={"character_id": "contract_test", "layers_dir": str(layer_dir)},
        )
        assert r.status_code == 200
        body = r.json()
        data = body["data"]
        assert data["success"] is True, f"导出失败: {data}"
        assert data["model3_json"], f"缺少 model3_json: {data}"
        assert data["model3_json"].startswith("/output/")

    def test_download_returns_zip(self, client, isolated_output, layer_dir):
        r = client.post(
            "/api/export/live2d",
            json={
                "character_id": "contract_test",
                "layers_dir": str(layer_dir),
                "download": True,
            },
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert r.content[:2] == b"PK"

    def test_missing_layers_reports_failure_not_crash(self, client, isolated_output):
        r = client.post("/api/export/live2d", json={"character_id": "empty"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["success"] is False
        assert "positional argument" not in str(data.get("message", ""))

    def test_exported_model3_is_retrievable(self, client, isolated_output, layer_dir):
        r = client.post(
            "/api/export/live2d",
            json={"character_id": "contract_test", "layers_dir": str(layer_dir)},
        )
        model3_url = r.json()["data"]["model3_json"]
        fetched = client.get(model3_url)
        assert fetched.status_code == 200
        assert fetched.json()["Version"] == 3


class TestChatContract:
    """对话端点的调用签名与返回契约（曾因构造参数不匹配而永久降级）"""

    def test_chat_returns_wellformed_envelope(self, client):
        r = client.post("/api/chat", json={"message": "你好", "history": []})
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        data = body["data"]
        assert isinstance(data.get("reply"), str) and data["reply"]
        assert isinstance(data.get("emotion"), str)

    def test_chat_does_not_leak_signature_errors(self, client):
        r = client.post("/api/chat", json={"message": "hello", "history": []})
        reply = r.json()["data"]["reply"]
        assert "positional argument" not in reply, f"构造签名漂移: {reply}"
        assert "unexpected keyword argument" not in reply, f"调用签名漂移: {reply}"

    def test_stream_emits_sse_and_finishes(self, client):
        r = client.post("/api/chat/stream", json={"message": "hi", "history": []})
        assert r.status_code == 200
        assert "data:" in r.text
        assert '"finished": true' in r.text
