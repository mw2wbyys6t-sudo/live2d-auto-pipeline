#!/usr/bin/env python3
"""P3 后端收口回归：M6 产物唯一命名/陈旧清理 + M7 通用 WS 连接上限。

双向原则：攻击/异常路径被拦住的同时，正常业务路径必须照常工作。
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from starlette.websockets import WebSocketDisconnect

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import api_server as srv  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ---------- M6：唯一命名 ----------
def test_generate_id_is_collision_free_and_uuid_shaped():
    ids = {srv.generate_id("export_") for _ in range(200)}
    # 秒级时间戳在同一秒内必然碰撞；uuid12 连续取 200 个必须全部不同
    assert len(ids) == 200
    pattern = re.compile(r"^export_[0-9a-f]{12}$")
    assert all(pattern.match(i) for i in ids)


# ---------- M6：陈旧产物清理 ----------
def _set_old_mtime(p: Path) -> None:
    old = time.time() - srv.EXPORT_ARTIFACT_TTL_SEC - 60
    os.utime(p, (old, old))


def test_cleanup_removes_stale_exports_and_zips_keeps_fresh(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    tmpzone = tmp_path / "tmpzone"
    tmpzone.mkdir()
    monkeypatch.setattr(srv, "OUTPUT_DIR", out)
    monkeypatch.setattr(srv.tempfile, "gettempdir", lambda: str(tmpzone))

    stale_dir = out / "export_stale"
    stale_dir.mkdir()
    (stale_dir / "character.model3.json").write_text("x", encoding="utf-8")
    fresh_dir = out / "export_fresh"
    fresh_dir.mkdir()
    (fresh_dir / "character.model3.json").write_text("x", encoding="utf-8")
    stale_zip = out / "live2d_stale.zip"
    stale_zip.write_bytes(b"x")
    fresh_zip = out / "live2d_fresh.zip"
    fresh_zip.write_bytes(b"x")
    stale_tmp_zip = tmpzone / "live2d_crash_leftover.zip"
    stale_tmp_zip.write_bytes(b"x")
    for p in (stale_dir, stale_zip, stale_tmp_zip):
        _set_old_mtime(p)

    srv._cleanup_old_export_artifacts()

    # 过期产物（含崩溃残留在系统临时目录的下载 zip）被清
    assert not stale_dir.exists(), "TTL 过期的导出目录应被删除"
    assert not stale_zip.exists(), "TTL 过期的输出根遗留 zip 应被删除"
    assert not stale_tmp_zip.exists(), "临时目录残留 zip 应被清"
    # 新鲜产物必须保留（正常业务不被误伤）
    assert fresh_dir.exists(), "未过期的导出目录不能被清理"
    assert (fresh_dir / "character.model3.json").exists()
    assert fresh_zip.exists(), "未过期的 zip 不能被清理"


def test_cleanup_swallows_errors_and_is_sweep_locked(tmp_path, monkeypatch):
    # 不可读/不存在的场景都不允许抛异常拖垮导出主链路
    monkeypatch.setattr(srv, "OUTPUT_DIR", tmp_path / "missing")
    monkeypatch.setattr(srv.tempfile, "gettempdir", lambda: str(tmp_path / "no-tmp"))
    srv._cleanup_old_export_artifacts()  # 不抛即通过

    # 拿锁后第二次并发扫描应直接跳过（避免并发导出重复扫盘）
    assert srv._EXPORT_SWEEP_LOCK.acquire(blocking=False)
    try:
        called = {"n": 0}
        real_glob = Path.glob

        def counting_glob(self, pattern):  # noqa: ANN001
            called["n"] += 1
            return real_glob(self, pattern)

        monkeypatch.setattr(Path, "glob", counting_glob)
        srv._cleanup_old_export_artifacts()
        assert called["n"] == 0
    finally:
        srv._EXPORT_SWEEP_LOCK.release()


def test_safe_unlink_removes_file_and_swallows_missing(tmp_path):
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    srv._safe_unlink(f)
    assert not f.exists()
    srv._safe_unlink(f)  # 重复删除不抛异常


# ---------- M6：download=true 的 zip 走临时目录且发送后即删 ----------
def test_export_download_zip_unique_named_and_cleaned_up(ws_client, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    layers = out / "layers_xyz"
    layers.mkdir()
    Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(layers / "body.png")
    monkeypatch.setattr(srv, "OUTPUT_DIR", out)
    # 跳过重构建：只验证打包/命名/清理链路。
    # 必须给出就绪标记 —— 打包闸门会（正确地）拒绝没有内核验收结论的产物，
    # 那是另一条测试的责任，不该在这里顺带触发。
    monkeypatch.setattr(
        srv,
        "_build_live2d_model",
        lambda layers_, output_dir, name: {
            "model3_json": "",
            "textures": [],
            "moc3": {"moc3_written": True, "runtime_ready": True, "moc3_blocker": None},
        },
    )

    r = ws_client.post(
        "/api/export/live2d",
        json={"layers_dir": str(layers), "download": True, "character_id": "c1"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    cd = r.headers["content-disposition"]
    assert "live2d_" in cd and cd.endswith('.zip"'), cd
    # 输出根不得残留任何 zip（临时文件在响应后台任务中删除）
    assert list(out.glob("*.zip")) == []
    # 导出目录为 uuid 命名而非秒级时间戳
    export_dirs = [p.name for p in out.glob("export_*")]
    assert export_dirs and all(re.match(r"export_[0-9a-f]{12}$", n) for n in export_dirs)


# ---------- M7：/api/ws 通用通道连接上限 ----------
@pytest.fixture()
def ws_client():
    with TestClient(srv.app) as c:
        yield c


def test_generic_ws_enforces_connection_cap(ws_client, monkeypatch):
    monkeypatch.setattr(srv._GenericWSState, "CLIENT_LIMIT", 1)
    try:
        with ws_client.websocket_connect("/api/ws") as first:
            assert first.receive_json()["type"] == "connected"
            # 第二条连接必须在 accept 之前被 1013 拒绝
            with pytest.raises(WebSocketDisconnect) as exc:
                with ws_client.websocket_connect("/api/ws"):
                    pass
            assert exc.value.code == 1013
    finally:
        monkeypatch.setattr(srv._GenericWSState, "CLIENT_LIMIT", 100)
    # 连接关闭后槽位必须释放，后续合法连接不受影响
    assert srv._GenericWSState.clients == 0
    with ws_client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "connected"
    assert srv._GenericWSState.clients == 0


def test_generic_ws_limit_env_override_and_fallback(monkeypatch):
    monkeypatch.setenv("LIVE2D_WS_CLIENT_LIMIT", "3")
    assert srv._GenericWSState._effective_limit() == 3
    for bad in ("0", "-5", "abc"):
        monkeypatch.setenv("LIVE2D_WS_CLIENT_LIMIT", bad)
        assert srv._GenericWSState._effective_limit() == 100
    monkeypatch.delenv("LIVE2D_WS_CLIENT_LIMIT", raising=False)
    assert srv._GenericWSState._effective_limit() == 100


def test_generic_ws_still_rejects_cross_site_origin(ws_client):
    # 上限加固不得削弱既有 Origin 闸门
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(
            "/api/ws", headers={"Origin": "http://evil.example"}
        ):
            pass
    assert exc.value.code == 1008
