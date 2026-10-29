"""GET /api/gpu 的行为：报告真实探测结果，且不在每次轮询时重跑 WMI。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import api_server as srv  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_FAKE = {
    "available": True,
    "adapters": [{
        "name": "NVIDIA GeForce RTX 2060",
        "driver_version": "32.0.15.8183",
        "vram_bytes": 4293918720,
        "vendor": "NVIDIA",
    }],
    "runtime": {
        "onnxruntime_available": True,
        "onnxruntime_providers": ["CPUExecutionProvider"],
        "torch_available": False,
        "torch_cuda_available": False,
        "torch_device_name": None,
    },
    "recommendation": {"device": "cpu"},
}


def _reset_gpu_cache() -> None:
    srv._PROBES._items.pop("gpu", None)


def test_gpu_endpoint_reports_probe_result(monkeypatch):
    """端点必须把探测结构原样报出去，前端据此显示卡型与推荐设备。"""
    _reset_gpu_cache()
    monkeypatch.setattr(srv, "_probe_gpu", lambda: _FAKE)
    with TestClient(srv.app) as c:
        body = c.get("/api/gpu").json()

    assert body["success"] is True
    assert body["data"]["available"] is True
    assert body["data"]["adapters"][0]["name"] == "NVIDIA GeForce RTX 2060"
    assert body["data"]["recommendation"]["device"] == "cpu"
    _reset_gpu_cache()


def test_diagnostics_includes_gpu_row_flagging_cpu_fallback(monkeypatch):
    """自检页必须出现 GPU 行，且「有卡但只能跑 CPU」记为不通过。"""
    _reset_gpu_cache()
    monkeypatch.setattr(srv, "_probe_gpu", lambda: _FAKE)
    with TestClient(srv.app) as c:
        body = c.get("/api/diagnostics").json()

    rows = {row["key"]: row for row in body["data"]["checks"]}
    assert "gpu" in rows, "自检项应与 Go 侧一样包含 GPU 行"
    assert rows["gpu"]["ok"] is False
    joined = " ".join(rows["gpu"].get("issues", []))
    assert "RTX 2060" in joined and "CPU" in joined
    _reset_gpu_cache()


def test_diagnostics_gpu_row_survives_probe_failure(monkeypatch):
    """探测抛异常时自检页不能整页失败，也不能回显异常原文。"""
    _reset_gpu_cache()

    def boom():
        raise RuntimeError("脚本执行失败: C:\\Users\\secret\\python.exe")

    monkeypatch.setattr(srv, "_probe_gpu", boom)
    with TestClient(srv.app) as c:
        body = c.get("/api/diagnostics").json()

    rows = {row["key"]: row for row in body["data"]["checks"]}
    assert rows["gpu"]["ok"] is False
    assert "secret" not in str(rows["gpu"])
    _reset_gpu_cache()


def test_gpu_endpoint_reuses_cached_probe_across_requests(monkeypatch):
    """WMI 查询要几百毫秒，轮询期间必须命中 TTL 缓存而不是反复采集。"""
    calls = []

    def counting_probe():
        calls.append(1)
        return _FAKE

    _reset_gpu_cache()
    monkeypatch.setattr(srv, "_probe_gpu", counting_probe)
    with TestClient(srv.app) as c:
        c.get("/api/gpu")
        c.get("/api/gpu")
        assert len(calls) == 1
    _reset_gpu_cache()
