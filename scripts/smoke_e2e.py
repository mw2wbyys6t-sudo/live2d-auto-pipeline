#!/usr/bin/env python3
"""Live2D Master Agent — CI 端到端冒烟（P1 验收闸门，确定性、可断网运行）。

用法：
    LIVE2D_SMOKE=1 python scripts/smoke_e2e.py --base http://127.0.0.1:8080 \
        --out C:\\live2d-smoke

断言（任一失败即非零退出，不接受 skip 式假绿）：
  1. /api/health 在超时内转绿（Python 探针 PIL/numpy 可用）；
  2. POST /api/generate/character 成功，立绘文件真实存在且非空；
     LIVE2D_SMOKE=1 时 provider 必须是 local_placeholder（确定性占位图，断网可复现）；
  3. GET /api/generations/latest 回传 layers_dir / model3 记录；
  4. POST /api/export/live2d（download=true）产出 zip，内含非空 moc3 / model3.json / 纹理；
  5. （可选 --verify-model）用内置 live2d-py 跑 verify.py 逐像素判据全绿。

本脚本只依赖 requests（CI 环境已装）；不 import 仓库内部模块，避免耦合。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import zipfile
import io
from pathlib import Path

import requests


def wait_health(base: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            r = requests.get(base + "/api/health", timeout=10)
            if r.status_code == 200 and r.json().get("success"):
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:  # 连接未就绪是常态，持续轮询
            last = str(exc)
        time.sleep(2)
    raise SystemExit(f"❌ 健康检查在 {timeout}s 内未转绿。最后状态: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--out", default="", help="产物校验目录（可选，默认临时目录）")
    ap.add_argument("--timeout", type=int, default=180, help="健康检查超时秒数")
    ap.add_argument("--verify-core", action="store_true",
                    help="导出后用内置 live2d-py 跑官方内核一致性检查（无 GL，本地/CI 可跑）")
    ap.add_argument("--verify-model", action="store_true",
                    help="导出后调用内置 live2d-py verify.py 跑逐像素判据（需 GL/xvfb）")
    ap.add_argument("--python", default="", help="verify.py 使用的解释器（默认 sys.executable）")
    ap.add_argument("--app-dir", default="", help="app 源码树（定位 drivers/live2d_runtime/verify.py）")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    print(f"[1/5] 轮询 {base}/api/health ...")
    health = wait_health(base, args.timeout)
    print(f"      ✓ 健康检查绿: {health.get('data', {}).get('version')}")

    smoke = os.environ.get("LIVE2D_SMOKE") == "1"
    print("[2/5] POST /api/generate/character（确定性种子）...")
    payload = {
        "prompt": "smoke test character, blue hair, white background",
        "width": 512,
        "height": 512,
        "seed": 42,
        "use_semantic": True,
    }
    r = requests.post(base + "/api/generate/character", json=payload, timeout=600)
    if r.status_code != 200:
        raise SystemExit(f"❌ 生图失败 HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    if not body.get("success"):
        raise SystemExit(f"❌ 生图未成功: {body.get('error')}")
    data = body.get("data") or {}
    image_path = data.get("image_path") or ""
    if not image_path or not Path(image_path).is_file() or Path(image_path).stat().st_size == 0:
        raise SystemExit(f"❌ 立绘文件缺失或为空: {image_path}")
    provider = (data.get("steps") or {}).get("generate", {}).get("provider", "")
    if smoke and provider != "local_placeholder":
        raise SystemExit(f"❌ LIVE2D_SMOKE=1 下 provider 应为 local_placeholder，实际 {provider!r}")
    print(f"      ✓ 立绘 {Path(image_path).name} ({Path(image_path).stat().st_size} bytes, provider={provider})")

    print("[3/5] GET /api/generations/latest ...")
    r = requests.get(base + "/api/generations/latest", timeout=30)
    if r.status_code != 200 or not r.json().get("success"):
        raise SystemExit(f"❌ latest 记录获取失败: {r.status_code} {r.text[:200]}")
    latest = (r.json().get("data") or {})
    if not latest.get("model3_json") and not latest.get("output_dir"):
        raise SystemExit("❌ latest 记录缺少 model3_json/output_dir 接力字段")
    print(f"      ✓ latest 接力: model3={Path(latest.get('model3_json','')).name}")

    print("[4/5] POST /api/export/live2d（download=true）...")
    export_req = {
        "character_id": "smoke",
        "layers_dir": latest.get("layers_dir") or data.get("layers_dir") or "",
        "download": True,
    }
    r = requests.post(base + "/api/export/live2d", json=export_req, timeout=600)
    if r.status_code != 200:
        raise SystemExit(f"❌ 导出失败 HTTP {r.status_code}: {r.text[:300]}")
    if "zip" not in r.headers.get("Content-Type", ""):
        raise SystemExit(f"❌ 导出未返回 zip: {r.headers.get('Content-Type')}")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    moc3 = [n for n in names if n.endswith(".moc3")]
    model3 = [n for n in names if n.endswith(".model3.json")]
    textures = [n for n in names if n.endswith(".png")]
    if not moc3 or not model3:
        raise SystemExit(f"❌ zip 内缺少 moc3/model3.json: {names[:10]}")
    moc_bytes = zf.read(moc3[0])
    if moc_bytes[:4] != b"MOC3" or len(moc_bytes) < 1024:
        raise SystemExit(f"❌ moc3 头非法或过小: {len(moc_bytes)} bytes")
    if not textures:
        raise SystemExit("❌ zip 内无纹理 PNG")
    print(f"      ✓ zip {len(names)} 项: moc3 {len(moc_bytes)}B, model3, {len(textures)} 纹理")

    # 5. 内置内核验收：--verify-core（一致性，无 GL）与 --verify-model（逐像素，需 GL）
    import subprocess
    out_dir = Path(args.out) or Path(tempfile.mkdtemp(prefix="live2d-verify-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    zf.extractall(out_dir)
    py = args.python or sys.executable
    app_dir = Path(args.app_dir) if args.app_dir else Path(__file__).resolve().parent.parent

    if args.verify_core:
        print("[5a] 内置 live2d-py 官方内核一致性检查（无 GL）...")
        consistency_py = app_dir / "drivers" / "live2d_runtime" / "moc3_consistency.py"
        if not consistency_py.is_file():
            raise SystemExit(f"❌ 找不到 moc3_consistency.py: {consistency_py}")
        proc = subprocess.run(
            [py, "-m", "drivers.live2d_runtime.moc3_consistency", str(out_dir / moc3[0])],
            capture_output=True, text=True, timeout=120, cwd=str(app_dir),
        )
        if proc.returncode != 0:
            print("STDOUT:", proc.stdout[-1000:])
            print("STDERR:", proc.stderr[-1000:])
            raise SystemExit(f"❌ 官方内核一致性检查失败（退出码 {proc.returncode}）")
        if '"ok": true' not in proc.stdout and '"ok":true' not in proc.stdout:
            raise SystemExit(f"❌ 一致性输出未含 ok:true: {proc.stdout[-300:]}")
        print("      ✓ 官方 Cubism Core 接受该 moc3（HasMocConsistencyFromFile = true）")

    if args.verify_model:
        print("[5b] 内置 live2d-py 逐像素验收（需 GL/xvfb）...")
        verify_py = app_dir / "drivers" / "live2d_runtime" / "verify.py"
        if not verify_py.is_file():
            raise SystemExit(f"❌ 找不到 verify.py: {verify_py}")
        manifest = out_dir / model3[0]
        report = out_dir / "verify-report.json"
        proc = subprocess.run(
            [py, str(verify_py), str(manifest), "--report", str(report)],
            capture_output=True, text=True, timeout=300, cwd=str(app_dir),
        )
        result = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
        if not result.get("runtime_verified"):
            print("STDOUT:", proc.stdout[-1000:])
            print("STDERR:", proc.stderr[-1000:])
            raise SystemExit(f"❌ 官方内核逐像素验收未全绿: {result}")
        checks = result.get("checks") or []
        print(f"      ✓ verify.py 全绿: {len(checks)} 参数逐像素通过")

    if not (args.verify_core or args.verify_model):
        print("[5] 跳过内置内核验收（未指定 --verify-core / --verify-model）")

    print("\n✅ 冒烟全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
