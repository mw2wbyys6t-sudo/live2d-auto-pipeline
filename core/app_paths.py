"""应用级路径解析（产品名 Pygmalion Studio）。

改名前后老用户的数据必须还在原处被读到，所以这里的规则是固定的一条：
**新目录优先，旧目录只要存在就继续沿用**。绝不「找不到新目录就新建一个」——
那会让角色库/产物/密钥在用户眼里凭空消失。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Optional

#: 产品显示名与各平台的目录名（唯一真源，其余模块从这里取）。
APP_DISPLAY_NAME = "Pygmalion Studio"
APP_DIR_NAME = "PygmalionStudio"

#: 改名前的历史目录名。存在即沿用，不做搬迁。
LEGACY_DIR_NAMES = ("live2d-master-agent", "Live2DMasterAgent")


def _candidate_bases(platform: str, env: Mapping[str, str], home: Path) -> list[Path]:
    """按平台给出候选根目录（顺序即优先级）。

    Windows 同时看 %APPDATA% 与主目录：历史实现里 Go 用 %APPDATA%、
    Python 用主目录，两边本来就不一致；把两个都列为候选，改名才不会
    把某一侧的数据留在原地无人认领。
    """
    if platform == "win32":
        appdata = env.get("APPDATA")
        bases = [Path(appdata)] if appdata else [home / "AppData" / "Roaming"]
        return bases + [home]
    if platform == "darwin":
        return [home / "Library" / "Application Support", home]
    xdg = env.get("XDG_DATA_HOME")
    bases = [Path(xdg)] if xdg else [home / ".local" / "share"]
    xdg_config = env.get("XDG_CONFIG_HOME")
    if xdg_config:
        bases.insert(0, Path(xdg_config))
    return bases + [home]


def data_root_for_platform(
    platform: str,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """数据/产物根目录：显式覆盖 > 已存在的新/旧目录 > 新目录（尚未创建）。"""
    environment = dict(os.environ) if env is None else dict(env)
    override = environment.get("LIVE2D_DATA_DIR", "").strip()
    if override:
        return Path(override)
    base_home = Path(home) if home else Path(environment.get("HOME") or Path.home())
    for base in _candidate_bases(platform, environment, base_home):
        for name in (APP_DIR_NAME, *LEGACY_DIR_NAMES):
            candidate = base / name
            if candidate.is_dir():
                return candidate
    return _candidate_bases(platform, environment, base_home)[0] / APP_DIR_NAME


def data_root() -> Path:
    """当前运行环境下的数据根目录。"""
    return data_root_for_platform(sys.platform)
