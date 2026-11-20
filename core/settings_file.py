"""读取 UI 设置文件（非敏感项）并覆盖到配置/环境变量。

Go 后端把设置页保存的非敏感项写到 ``settings.json``，启动时通过
``LIVE2D_SETTINGS_FILE`` 把路径交给 Python 子进程。这里把它映射成
既有的环境变量键（ComfyUI/SD 地址、OpenAI 兼容端点、Seedream 版本等），
保证：

* 用户在设置页的修改**无需重启**即可生效（每次生成都是新进程）；
* 优先级为 **UI 设置 > .env > 进程环境变量**（见 ``core/config.py`` 的加载顺序）。

本模块**只处理非敏感项**。API Key 一律走 ``core/secure_storage.py`` 的
加密保险库，绝不写进 settings.json，也不在这里出现。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

SETTINGS_ENV = "LIVE2D_SETTINGS_FILE"

#: settings.json 字段 -> 环境变量名。只映射 Python 侧真正会读的键。
_ENGINE_MAP = (
    ("comfyui_base_url", "COMFYUI_BASE_URL"),
    ("sd_webui_base_url", "SD_WEBUI_BASE_URL"),
)
_OPENAI_MAP = (
    ("base_url", "OPENAI_BASE_URL"),
    ("model", "IMAGE_MODEL"),
)
_SEEDREAM_MAP = (
    ("version", "SEEDREAM_VERSION"),
    ("size", "SEEDREAM_SIZE"),
)
_SENSENOVA_MAP = (("base_url", "SENSENOVA_BASE_URL"),)


def _clean(value: object, limit: int = 300) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        return ""
    # 控制字符会破坏环境变量与下游 HTTP 头，直接丢弃整项。
    if any(ord(ch) < 0x20 for ch in text):
        return ""
    return text


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def settings_path() -> Optional[Path]:
    raw = os.environ.get(SETTINGS_ENV, "").strip()
    return Path(raw) if raw else None


def load_overrides(path: Optional[Path] = None) -> Dict[str, str]:
    """把 settings.json 翻译成 ``{环境变量: 值}``；缺失/损坏时返回空表。"""
    target = path or settings_path()
    if target is None or not target.is_file():
        return {}
    try:
        # utf-8-sig：用户用记事本手改过 settings.json 会带 BOM，
        # 按 utf-8 读会直接 JSONDecodeError 并静默丢弃全部设置。
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    out: Dict[str, str] = {}
    engines = _section(data, "engines")
    for src, env in _ENGINE_MAP:
        value = _clean(engines.get(src))
        if value:
            out[env] = value

    providers = _section(data, "providers")
    for src, env in _OPENAI_MAP:
        value = _clean(_section(providers, "openai").get(src))
        if value:
            out[env] = value
    for src, env in _SEEDREAM_MAP:
        value = _clean(_section(providers, "seedream").get(src), limit=64)
        if value:
            out[env] = value
    for src, env in _SENSENOVA_MAP:
        value = _clean(_section(providers, "sensenova").get(src))
        if value:
            out[env] = value
    return out


def apply_overrides(config) -> None:
    """把 overrides 应用到 ``SecureConfig``（需在 .env 加载之后调用）。"""
    values = load_overrides()
    if values:
        config.apply_overrides(values)
