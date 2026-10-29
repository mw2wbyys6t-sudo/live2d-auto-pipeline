"""降级模式（api_server.py）的接口契约测试。

背景：Go 服务（api/）是生产后端，Python 服务（api_server.py）是降级替代实现。
两者对前端暴露同一套 HTTP/WS 契约。此前两者在多处不一致，导致降级模式下：

- `/api/health` 不带 `python_ok` —— 安装验收脚本
  （scripts/windows/verify_desktop_install.ps1）与 e2e checklist 必然误判 FAIL；
- `/api/status` 把四个服务全部硬编码成 ``available: True`` —— 「装了但跑不动」看不出来；
- `/api/models` 把 seedream/sensenova 硬编码成 ``False`` —— 配了 Key 仍显示不可用；
- 前端实际调用的 8 个端点返回 404 —— 健康页误报「无法连接后端」；
- `/api/ws` 只有阻塞的接收循环，不推 ``tracking_frame`` —— 前端连上了却永远收不到帧，
  表现为「连着但模型不动」（web/lib/native-tracker.ts 只认 /api/ws 上的 tracking_frame）。

这些缺陷在编译期完全不可见，只在运行时暴露，因此必须由契约测试锁住。
断言的对象是**响应结构与真实来源**，不是「返回 200 就算过」。
"""

from __future__ import annotations

import json
import sys
import threading
import time
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


def _png_bytes(size=(64, 64), colour=(200, 120, 90, 255)) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, "PNG")
    return buf.getvalue()


# ---------- /api/health：安装验收的硬判据 ----------

class TestHealthContract:
    def test_reports_python_ok(self, client):
        """python_ok 是 verify_desktop_install.ps1 与 e2e checklist 的判据，缺字段即 FAIL。"""
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["python_ok"] is True

    def test_reports_503_when_dependencies_missing(self, client):
        """依赖缺失必须回 503 + 明确原因，不能装作一切正常。"""
        api_server._PROBES._items["python_env"] = (
            time.monotonic(),
            {"ok": False, "issues": ["缺少依赖: PIL（ImportError）"],
             "version": "3.12.0", "executable": "/usr/bin/python3"},
        )
        try:
            r = client.get("/api/health")
        finally:
            api_server._PROBES._items.pop("python_env", None)
        assert r.status_code == 503
        body = r.json()
        assert body["success"] is False
        assert body["data"]["python_ok"] is False
        assert "PIL" in body["error"]


# ---------- /api/status：可用性必须是实测的 ----------

class TestSystemStatusContract:
    def test_every_service_reports_availability_and_version(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        services = r.json()["data"]["services"]
        assert services, "services 不能为空"
        for svc in services:
            assert isinstance(svc["name"], str) and svc["name"]
            assert isinstance(svc["available"], bool), f"{svc['name']} 的 available 必须是布尔"
            assert isinstance(svc["version"], str), f"{svc['name']} 必须带可读的 version 说明"

    def test_availability_is_not_hardcoded_true(self, client):
        """把 python_env 探测成失败，服务列表里必须出现 available=False。"""
        api_server._PROBES._items["python_env"] = (
            time.monotonic(),
            {"ok": False, "issues": ["缺少依赖: numpy"],
             "version": "3.12.0", "executable": "/usr/bin/python3"},
        )
        try:
            services = client.get("/api/status").json()["data"]["services"]
        finally:
            api_server._PROBES._items.pop("python_env", None)
        by_name = {s["name"]: s for s in services}
        assert by_name["python_env"]["available"] is False
        assert "numpy" in by_name["python_env"]["version"]


# ---------- /api/models：模型可用性跟随真实 provider ----------

class TestModelsContract:
    def test_availability_matches_provider_probe(self, client):
        """available 必须来自 ProviderRouter 的实测，而不是写死。"""
        from core.image_gen.router import ProviderRouter

        info = {d["name"]: d for d in ProviderRouter().get_provider_info()}
        models = client.get("/api/models").json()["data"]
        by_id = {m["id"]: m for m in models}
        for pid in ("pollinations", "seedream", "sensenova"):
            assert pid in by_id, f"模型清单缺 {pid}"
            assert by_id[pid]["available"] == bool(info[pid]["available"]), (
                f"{pid} 的可用性与 provider 实测不一致")
            assert by_id[pid]["requires_key"] == bool(info[pid]["requires_key"])


# ---------- /api/info：能力声明必须跟得上实际 ----------

class TestApiInfoContract:
    def test_features_reflect_probe_results_not_hardcoded(self, client):
        """语义分层降级时要如实标成降级，不能仍宣称「语义分层」。"""
        api_server._PROBES._items["segment_engine"] = (
            time.monotonic(),
            {"ok": True, "degraded": True,
             "detail": "未找到 ONNX 前景模型，分层退化为颜色启发式（精度低）"},
        )
        try:
            features = client.get("/api/info").json()["data"]["features"]
        finally:
            api_server._PROBES._items.pop("segment_engine", None)
        assert not any(f == "语义分层" for f in features), (
            f"降级时不得宣称语义分层: {features}")
        assert any("颜色启发式分层" in f for f in features), (
            f"降级必须被如实标出: {features}")

    def test_endpoint_map_is_present(self, client):
        endpoints = client.get("/api/info").json()["data"]["endpoints"]
        assert endpoints["health"] == "GET /api/health"
        assert endpoints["status"] == "GET /api/status"


# ---------- /api/diagnostics：与 Go 侧同构的 9 项自检 ----------

class TestDiagnosticsContract:
    _GO_CHECK_KEYS = [
        "backend", "python", "scripts_dir", "output_dir",
        "tracker_exe", "tracker_model", "tracker_dll", "gpu", "bind_loopback",
    ]

    def test_check_keys_match_go_backend(self, client):
        """前端自检页逐条渲染 checks；键名与 Go 侧不一致会让两套后端的显示项跳变。"""
        r = client.get("/api/diagnostics")
        assert r.status_code == 200
        data = r.json()["data"]
        keys = [c["key"] for c in data["checks"]]
        assert keys == self._GO_CHECK_KEYS, f"自检项与 Go 侧不一致: {keys}"

    def test_report_shape_matches_frontend_interface(self, client):
        """对齐 web/lib/api-client.ts 的 DiagnosticsReport / DiagnosticCheck。"""
        data = client.get("/api/diagnostics").json()["data"]
        assert isinstance(data["version"], str)
        assert isinstance(data["os"], str)
        assert isinstance(data["host"], str)
        assert isinstance(data["port"], int)
        assert isinstance(data["python_path"], str)
        assert isinstance(data["output_dir"], str)
        for check in data["checks"]:
            assert isinstance(check["key"], str)
            assert isinstance(check["label"], str)
            assert isinstance(check["ok"], bool)
            if "detail" in check:
                assert isinstance(check["detail"], str)
            if "issues" in check:
                assert isinstance(check["issues"], list)

    def test_output_dir_check_writes_a_probe(self, client, isolated_output):
        """产物目录可写性必须真的写一次文件，不能凭空断言。"""
        data = client.get("/api/diagnostics").json()["data"]
        out_check = next(c for c in data["checks"] if c["key"] == "output_dir")
        assert out_check["ok"] is True
        assert not (isolated_output / ".write-probe").exists(), "探针文件必须被清理"


# ---------- /api/providers / /api/models/optional ----------

class TestProvidersContract:
    def test_shape_matches_frontend_interface(self, client):
        data = client.get("/api/providers").json()["data"]
        assert data["query_ok"] is True
        assert isinstance(data["registered"], list)
        assert isinstance(data["available"], list)
        for item in data["available"]:
            assert isinstance(item["name"], str)
            assert isinstance(item["display_name"], str)
            assert isinstance(item["requires_key"], bool)


class TestOptionalModelsContract:
    def test_shape_matches_frontend_interface(self, client):
        """对齐 web/lib/api-client.ts 的 OptionalModel（前端按 state 分支渲染）。"""
        data = client.get("/api/models/optional").json()["data"]
        components = data["components"]
        assert components, "可选模型清单不能为空"
        for item in components:
            for field in ("name", "url", "rel_path", "note", "requires_pkg", "state"):
                assert isinstance(item[field], str), f"{field} 必须是字符串"
            assert isinstance(item["size_hint_mb"], int)
            assert isinstance(item["progress"], (int, float))
            assert isinstance(item["downloaded_mb"], (int, float))
            assert isinstance(item["total_mb"], (int, float))
            assert item["state"] in ("idle", "downloading", "ready", "error", "cancelled")

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/models/optional/download"),
            ("post", "/api/models/optional/cancel"),
        ],
    )
    def test_download_actions_are_honest_501(self, client, method, path):
        """下载能力只有 Go API 有实现：必须 501 + go_endpoint，不能假成功。"""
        r = getattr(client, method)(path, json={"name": "sam2-hiera-small"})
        assert r.status_code == 501
        body = r.json()
        assert body["success"] is False
        assert body["error"] == "not_implemented"
        assert body["go_endpoint"].startswith("/")


# ---------- /api/tracking/status、/api/generations/models ----------

class TestTrackingStatusContract:
    def test_shape_matches_frontend_interface(self, client):
        data = client.get("/api/tracking/status").json()["data"]
        assert isinstance(data["running"], bool)
        assert isinstance(data["available"], bool)
        assert isinstance(data["source"], str)
        assert isinstance(data["camera_id"], int)


class TestExportedModelsContract:
    def test_lists_exported_models(self, client, isolated_output):
        model_dir = isolated_output / "live2d_exports" / "char_a"
        model_dir.mkdir(parents=True)
        (model_dir / "char_a.model3.json").write_text("{}", encoding="utf-8")

        data = client.get("/api/generations/models").json()["data"]
        assert data["count"] == len(data["models"]) == 1
        entry = data["models"][0]
        assert entry["name"] == "char_a"
        assert entry["model3_json"].endswith("char_a.model3.json")
        assert entry["model3_url"].startswith("/output/")
        assert entry["output_dir"]


# ---------- /api/upload、/api/import/psd、角色参考图与 embedding ----------

class TestUploadContract:
    def test_saves_into_output_uploads(self, client, isolated_output):
        r = client.post("/api/upload", files={"file": ("ref.png", _png_bytes(), "image/png")})
        assert r.status_code == 200
        data = r.json()["data"]
        saved = Path(data["path"])
        assert saved.is_file()
        assert saved.parent == (isolated_output / "uploads").resolve()
        assert data["url"].startswith("/output/")

    def test_rejects_non_image_extension(self, client, isolated_output):
        r = client.post("/api/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
        assert r.status_code == 400
        assert r.json()["success"] is False

    def test_import_psd_rejects_non_psd(self, client, isolated_output):
        r = client.post("/api/import/psd", files={"file": ("x.txt", b"hello", "text/plain")})
        assert r.status_code == 400
        assert r.json()["success"] is False


class TestImportPNGsContract:
    """前端分层页的「导入多张 PNG」走 POST /api/import/pngs（字段名 files）。"""

    def test_builds_layer_set_on_a_shared_canvas(self, client, isolated_output):
        small = _png_bytes(size=(32, 48))
        big = _png_bytes(size=(64, 96), colour=(10, 200, 10, 255))
        r = client.post(
            "/api/import/pngs",
            files=[
                ("files", ("a.png", small, "image/png")),
                ("files", ("b.png", big, "image/png")),
            ],
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["layer_count"] == 2
        assert data["canvas"] == [64, 96], "画布尺寸必须取所有图层的最大值"
        layers = data["layers"]
        assert [l["name"] for l in layers] == ["a", "b"], "图层顺序必须与提交顺序一致"
        for layer in layers:
            path = Path(layer["path"])
            assert path.is_file()
            assert path.parent == (isolated_output / path.parent.name).resolve()
            # 所有图层规范到同一画布 —— Live2D 网格要求各层共享同一画布，
            # 异尺寸会被 moc3 构建器以「画布尺寸不一致」拒绝。
            with Image.open(path) as img:
                assert img.size == (64, 96)
            assert layer["url"].startswith("/output/")

    def test_rejects_unsupported_extension(self, client, isolated_output):
        r = client.post(
            "/api/import/pngs",
            files=[("files", ("a.gif", b"GIF89a", "image/gif"))],
        )
        assert r.status_code == 400
        assert r.json()["success"] is False

    def test_rejects_empty_upload(self, client, isolated_output):
        r = client.post("/api/import/pngs", files=[])
        assert r.status_code in (400, 422)
        body = r.json()
        assert body["success"] is False
        assert body["error"], "校验失败必须走统一信封并给出可读原因"
        assert body["data"] is None


class TestCharacterReferenceContract:
    @pytest.fixture
    def character(self, tmp_path, monkeypatch):
        chars = tmp_path / "characters"
        chars.mkdir()
        monkeypatch.setattr(api_server, "CHARACTERS_DIR", chars)
        (chars / "char_ref.json").write_text(
            json.dumps({"character_id": "char_ref", "name": "测试角色"}),
            encoding="utf-8",
        )
        return "char_ref"

    def test_reference_image_must_live_in_uploads(self, client, isolated_output, character):
        """参考图路径必须限制在 uploads 目录内，否则可借端点读本机任意文件。"""
        r = client.post(
            f"/api/characters/{character}/references",
            json={"image_path": "/etc/passwd", "view": "front"},
        )
        assert r.status_code == 400
        assert r.json()["success"] is False

    def test_unknown_view_is_rejected(self, client, isolated_output, character):
        uploaded = client.post(
            "/api/upload", files={"file": ("ref.png", _png_bytes(), "image/png")}
        ).json()["data"]["path"]
        r = client.post(
            f"/api/characters/{character}/references",
            json={"image_path": uploaded, "view": "sideways"},
        )
        assert r.status_code == 400

    def test_embedding_without_reference_reports_reason(self, client, isolated_output, character):
        r = client.post(f"/api/characters/{character}/embedding")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["has_embedding"] is False
        assert data["dim"] == 0
        assert data["reason"], "没有参考图时必须说明原因"


# ---------- /api/ws：面捕帧必须真的推出去 ----------

class TestWebSocketTrackingContract:
    """前端 web/lib/native-tracker.ts 只认 /api/ws 上的 tracking_frame。

    降级模式此前只有一个阻塞的接收循环 —— 前端连得上却永远收不到帧，
    表现为「连着但模型不动」。
    """

    def test_broadcasts_tracking_frame_while_tracking_runs(self, client):
        api_server._TrackerState.lock = threading.Lock()
        api_server._TrackerState.running = True
        api_server._TrackerState.params = {"ParamAngleX": 12.5, "ParamMouthOpenY": 0.3}
        api_server._TrackerState.blendshapes = {"jawOpen": 0.4}
        api_server._TrackerState.head_rotation = {"pitch": 1.0, "yaw": 2.0, "roll": 3.0}
        try:
            with client.websocket_connect("/api/ws") as ws:
                assert ws.receive_json()["type"] == "connected"
                frame = None
                for _ in range(6):
                    msg = ws.receive_json()
                    if msg.get("type") == "tracking_frame":
                        frame = msg
                        break
        finally:
            api_server._TrackerState.running = False
            api_server._TrackerState.reset()

        assert frame is not None, "跟踪运行时必须收到 tracking_frame"
        data = frame["data"]
        assert data["live2d_params"]["ParamAngleX"] == 12.5
        assert data["blendshapes"]["jawOpen"] == 0.4
        assert data["head_rotation"]["yaw"] == 2.0
        assert isinstance(data["timestamp"], int)

    def test_still_answers_ping(self, client):
        """通用通道的 ping/pong 能力不能因为加了推帧循环而丢。"""
        with client.websocket_connect("/api/ws") as ws:
            assert ws.receive_json()["type"] == "connected"
            ws.send_text(json.dumps({"type": "ping"}))
            seen = []
            for _ in range(6):
                msg = ws.receive_json()
                seen.append(msg.get("type"))
                if "pong" in seen:
                    break
            assert "pong" in seen, f"ping 没有得到 pong，收到 {seen}"
