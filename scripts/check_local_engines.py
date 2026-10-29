#!/usr/bin/env python3
"""本地引擎连通性自检（ComfyUI / SD WebUI / Ollama）。

用途：确认「本地生图」到底缺什么，而不是等生成时报一个看不懂的错。

用法：
    python scripts/check_local_engines.py

退出码：0 = 至少一个本地生图引擎可用；2 = 生图引擎都不可用。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.image_gen.base import GenerationError  # noqa: E402
from core.image_gen.comfyui import ComfyUIProvider  # noqa: E402
from core.image_gen.sd_webui import SDWebUIProvider  # noqa: E402


def check_comfyui() -> tuple[bool, str]:
    p = ComfyUIProvider()
    if not p.is_available(force=True):
        return False, f"未运行（{p.base_url}）"
    try:
        ckpt = p._resolve_checkpoint(required=False)  # noqa: SLF001 - 自检脚本
    except GenerationError as e:
        return True, f"已连接，但读取模型列表失败：{e}"
    if not ckpt:
        return False, "已连接，但 models/checkpoints 里没有任何模型"
    return True, f"已连接，checkpoint = {ckpt}"


def check_sd_webui() -> tuple[bool, str]:
    p = SDWebUIProvider()
    if not p.is_available(force=True):
        return False, f"未运行或未加 --api（{p.base_url}）"
    return True, "已连接"


def check_ollama() -> tuple[bool, str]:
    import os

    import requests

    url = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    try:
        resp = requests.get(f"{url}/api/tags", timeout=2)
        if resp.status_code != 200:
            return False, f"返回 HTTP {resp.status_code}（{url}）"
        models = [m.get("name", "") for m in (resp.json().get("models") or [])]
        return True, f"已连接，模型：{', '.join(models) if models else '(无)'}"
    except Exception as e:
        return False, f"未运行（{url}）：{e}"


def main() -> int:
    rows = [
        ("ComfyUI", check_comfyui()),
        ("SD WebUI", check_sd_webui()),
        ("Ollama", check_ollama()),
    ]
    print("本地引擎自检")
    print("-" * 60)
    for name, (ok, detail) in rows:
        print(f"[{'OK ' if ok else 'FAIL'}] {name:10} {detail}")
    print("-" * 60)

    image_ok = rows[0][1][0] or rows[1][1][0]
    if not image_ok:
        print("提示：本地生图需要 ComfyUI 或 SD WebUI 至少一个可用。")
        print("   ComfyUI：启动后把模型放进 models/checkpoints（RTX 2060 6G 建议 SD1.5）")
        print("   SD WebUI：启动参数加 --api")
        print("   只想跑通流程：设置 LIVE2D_LOCAL_ONLY=1，会自动用离线占位图")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
