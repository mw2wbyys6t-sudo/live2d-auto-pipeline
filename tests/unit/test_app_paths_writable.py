"""可写数据根与保险库位置的解析规则。

实测（2026-10-30，已安装的 v0.10.10）：日志躺在 ``F:\\Pygmalion Studio\\app\\logs\\
live2d.log``，而环境自检页告诉用户去 ``%APPDATA%\\PygmalionStudio\\logs`` 找 ——
那个目录在本机根本不存在。根因是 ``LIVE2D_PROJECT_ROOT`` 被同时当两件事用：
"代码在哪"（core/config.py 找 .env，需要项目根）和"能往哪写"（日志/密钥，
需要用户可写目录）。桌面版里前者就是安装目录：装到 Program Files 写不进去，
卸载还会把诊断证据和 API Key 密文一起删掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import app_paths


@pytest.fixture
def clean_env(monkeypatch):
    """清掉三个会影响解析的环境变量，让每个用例自己摆。"""
    for var in ("LIVE2D_DATA_DIR", "LIVE2D_PROJECT_ROOT", "LIVE2D_VAULT_KEY_FILE"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_data_dir_override_wins(clean_env, tmp_path):
    clean_env.setenv("LIVE2D_DATA_DIR", str(tmp_path / "data"))
    clean_env.setenv("LIVE2D_PROJECT_ROOT", str(tmp_path / "install"))

    assert app_paths.writable_data_root() == tmp_path / "data"


def test_dev_behaviour_unchanged_without_data_dir(clean_env, tmp_path):
    """没有 LIVE2D_DATA_DIR 时沿用历史行为：项目根。

    刻意不让它退回平台数据根 —— 否则开发者一升级，日志就凭空从仓库里
    搬到 %APPDATA%，比原来的问题更难查。
    """
    clean_env.setenv("LIVE2D_PROJECT_ROOT", str(tmp_path / "repo"))

    assert app_paths.writable_data_root() == tmp_path / "repo"


def test_vault_prefers_writable_root(clean_env, tmp_path):
    data = tmp_path / "data"
    clean_env.setenv("LIVE2D_DATA_DIR", str(data))

    assert app_paths.vault_file_path() == data / app_paths.VAULT_FILENAME


def test_vault_migrates_from_install_dir_and_keeps_original(clean_env, tmp_path):
    """旧密文存在时复制一份到可写根，**旧文件保留**。

    直接改路径会让已配置过 API Key 的用户发现"设置全没了"；
    复制失败也要继续在旧位置读写，不能两头都不认。
    """
    data = tmp_path / "data"
    install = tmp_path / "install"
    install.mkdir()
    legacy = install / app_paths.VAULT_FILENAME
    legacy.write_bytes(b"ciphertext-v1")
    clean_env.setenv("LIVE2D_DATA_DIR", str(data))
    clean_env.setenv("LIVE2D_PROJECT_ROOT", str(install))

    resolved = app_paths.vault_file_path()

    assert resolved == data / app_paths.VAULT_FILENAME
    assert resolved.read_bytes() == b"ciphertext-v1"
    assert legacy.is_file(), "搬迁必须是复制而不是移动"


def test_vault_falls_back_when_copy_blocked(clean_env, tmp_path, monkeypatch):
    """复制被挡（只读盘/权限）时退回旧位置，而不是抛错或指向空文件。"""
    data = tmp_path / "data"
    install = tmp_path / "install"
    install.mkdir()
    legacy = install / app_paths.VAULT_FILENAME
    legacy.write_bytes(b"ciphertext-v1")
    clean_env.setenv("LIVE2D_DATA_DIR", str(data))
    clean_env.setenv("LIVE2D_PROJECT_ROOT", str(install))

    def deny(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "write_bytes", deny)

    assert app_paths.vault_file_path() == legacy


def test_vault_uses_new_file_when_both_exist(clean_env, tmp_path):
    """两边都有时以可写根为准 —— 搬迁之后旧文件就是历史遗留。"""
    data = tmp_path / "data"
    data.mkdir()
    (data / app_paths.VAULT_FILENAME).write_bytes(b"new")
    install = tmp_path / "install"
    install.mkdir()
    (install / app_paths.VAULT_FILENAME).write_bytes(b"old")
    clean_env.setenv("LIVE2D_DATA_DIR", str(data))
    clean_env.setenv("LIVE2D_PROJECT_ROOT", str(install))

    resolved = app_paths.vault_file_path()

    assert resolved == data / app_paths.VAULT_FILENAME
    assert (install / app_paths.VAULT_FILENAME).read_bytes() == b"old"


def test_logger_uses_writable_root(clean_env, tmp_path):
    """日志目录必须跟着可写根走 —— 这是这次实测里真正坏掉的那一条。"""
    data = tmp_path / "data"
    clean_env.setenv("LIVE2D_DATA_DIR", str(data))
    clean_env.setenv("LIVE2D_PROJECT_ROOT", str(tmp_path / "install"))

    from core.logger import get_logger
    logger = get_logger("path_probe_writable_root")
    logger.info("写一条用于定位日志文件的探针")

    produced = data / "logs" / "live2d.log"
    assert produced.is_file(), f"日志没有落到 {produced}"
    assert "path_probe_writable_root" in produced.read_text(encoding="utf-8")
