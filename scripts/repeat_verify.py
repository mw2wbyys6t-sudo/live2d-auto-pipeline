#!/usr/bin/env python3
"""反复验证 harness：多轮「启动 → 生图 → 导出 → 内核一致性 → 停止 → 端口释放」循环。

目的：暴露单次跑通掩盖的偶发问题——端口占用/泄漏、文件锁、缓存污染、产物漂移。
每轮用独立端口 + 独立 LIVE2D_DATA_DIR，结束后断言端口释放、无残留进程。

用法：
    python scripts/repeat_verify.py --cycles 3 --binary /tmp/live2d-api-test \
        --config /tmp/test-config.json [--verify-core]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import requests


def wait_health(base: str, timeout: int = 60) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=5) as r:
                body = json.load(r)
                if body.get("success"):
                    return body
                last = str(body)[:120]
        except Exception as e:
            last = str(e)
        time.sleep(1.5)
    raise SystemExit(f"健康检查未转绿: {last}")


def cycle(idx: int, port: int, binary: str, config: str, app_dir: str, verify_core: bool) -> dict:
    data_dir = Path(f"/tmp/repeat-verify-{idx}")
    if data_dir.exists():
        import shutil
        shutil.rmtree(data_dir)
    env = dict(os.environ, LIVE2D_DATA_DIR=str(data_dir), LIVE2D_SMOKE="1")
    proc = subprocess.Popen(
        [binary, "-config", config, "-port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    result = {"cycle": idx, "port": port, "steps": []}
    try:
        base = f"http://127.0.0.1:{port}"
        h = wait_health(base)
        result["steps"].append(f"health={h['data']['version']}")

        r = requests.post(base + "/api/generate/character", json={
            "prompt": f"repeat verify cycle {idx}", "width": 512, "height": 512,
            "seed": 1000 + idx, "use_semantic": True,
        }, timeout=300)
        assert r.status_code == 200 and r.json().get("success"), f"生图失败: {r.text[:200]}"
        d = r.json()["data"]
        assert Path(d["image_path"]).is_file(), "立绘文件缺失"
        assert d["steps"]["generate"]["provider"] == "local_placeholder"
        result["steps"].append(f"generate provider={d['steps']['generate']['provider']}")

        latest = requests.get(base + "/api/generations/latest", timeout=30).json()["data"]
        assert latest.get("model3_json"), "latest 缺 model3"
        result["steps"].append("latest relay ok")

        x = requests.post(base + "/api/export/live2d", json={
            "character_id": f"smoke{idx}", "layers_dir": d.get("layers_dir", ""), "download": True,
        }, timeout=300)
        assert x.status_code == 200 and "zip" in x.headers.get("Content-Type", ""), "导出非 zip"
        import io, zipfile
        zf = zipfile.ZipFile(io.BytesIO(x.content))
        moc3 = [n for n in zf.namelist() if n.endswith(".moc3")]
        assert moc3 and zf.read(moc3[0])[:4] == b"MOC3", "moc3 头非法"
        result["steps"].append(f"export zip={len(zf.namelist())} items moc3={len(zf.read(moc3[0]))}B")

        if verify_core:
            import tempfile
            out = Path(tempfile.mkdtemp())
            zf.extractall(out)
            m3 = [n for n in zf.namelist() if n.endswith(".model3.json")][0]
            py = sys.executable
            p = subprocess.run(
                [py, "-m", "drivers.live2d_runtime.moc3_consistency", str(out / moc3[0])],
                capture_output=True, text=True, timeout=120, cwd=app_dir, env=env,
            )
            assert p.returncode == 0 and '"ok": true' in p.stdout, f"内核一致性失败: {p.stdout[-200:]}"
            result["steps"].append("core consistency ok")
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        # 端口释放断言
        time.sleep(1.5)
        assert not _port_listening(port), f"端口 {port} 未释放（泄漏）"
        assert proc.returncode is not None, f"cycle {idx} 进程未退出"
    result["ok"] = True
    return result


def _port_listening(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--binary", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--app-dir", default=".")
    ap.add_argument("--verify-core", action="store_true")
    ap.add_argument("--base-port", type=int, default=18100)
    args = ap.parse_args()

    failures = []
    for i in range(1, args.cycles + 1):
        port = args.base_port + i
        print(f"\n===== cycle {i} (port {port}) =====")
        try:
            r = cycle(i, port, args.binary, args.config, args.app_dir, args.verify_core)
            for s in r["steps"]:
                print(f"  ✓ {s}")
            print(f"  ✓ 端口 {port} 已释放，进程已退出")
        except AssertionError as e:
            failures.append((i, str(e)))
            print(f"  ✗ cycle {i} 失败: {e}")
            break
        except Exception as e:
            failures.append((i, repr(e)))
            print(f"  ✗ cycle {i} 异常: {e}")
            break

    print(f"\n===== {args.cycles} 轮验证完成，失败 {len(failures)} =====")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
