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


def writable_data_root() -> Path:
    """子进程写日志/保险库的根：**只改打包版，不动开发版**。

    Go 桌面版启动时会在进程环境里放 ``LIVE2D_DATA_DIR``（%APPDATA%\\…），
    子进程继承它 —— 于是日志与密钥落在可写用户目录，卸载不会连诊断证据
    和 vault.key 一起删掉，装在 Program Files 下也写得进去。

    没有该变量时（开发环境直接跑 Python、或跑老包）沿用历史行为：
    ``LIVE2D_PROJECT_ROOT`` → 仓库内的 ``logs/``。这里刻意不退回到
    :func:`data_root`，否则开发者一升级就找不到日志了。
    """
    override = os.environ.get("LIVE2D_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path(os.environ.get("LIVE2D_PROJECT_ROOT", str(Path.home())))


VAULT_FILENAME = ".env.encrypted"


def vault_file_path() -> Path:
    """加密配置（API Key 密文）的存放位置，带一次**非破坏式**搬迁。

    历史位置是 ``LIVE2D_PROJECT_ROOT/.env.encrypted`` —— 桌面版的项目根就是
    安装目录，于是密钥密文躺在 ``C:\\Program Files\\...`` 里：写不进去、
    卸载即消失。新位置是 :func:`writable_data_root`。

    规则与整个模块一致 —— 绝不让用户眼里的数据凭空消失：
    新位置已有文件就用新的；只有旧位置有文件时**复制**一份过去（旧文件保留，
    所以搬迁失败也不会比现状更糟），然后开始用新位置。
    随机主密钥本来就在 ``%APPDATA%``，与密文是否搬家无关，所以复制过去仍解得开。
    """
    new_path = writable_data_root() / VAULT_FILENAME
    if new_path.exists():
        return new_path

    legacy_root = os.environ.get("LIVE2D_PROJECT_ROOT", "").strip()
    legacy_path = Path(legacy_root) / VAULT_FILENAME if legacy_root else None
    if legacy_path is None or not legacy_path.is_file():
        return new_path

    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(legacy_path.read_bytes())
    except OSError:
        # 搬不动就继续在旧位置读写：宁可留着已知缺陷，也不要让用户丢配置。
        return legacy_path
    return new_path
