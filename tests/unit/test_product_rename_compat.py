"""产品改名（Pygmalion Studio）后的路径兼容与命名一致性。

改名不能把老用户的东西留在原地：密钥库、数据目录、产物目录都必须
「新位置优先、旧位置仍可用」，否则已存的 API Key 与角色库会凭空消失。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.secure_storage import master_key_path_for_platform
from core.app_paths import data_root_for_platform

DISPLAY_NAME = "Pygmalion Studio"
NEW_DIR = "PygmalionStudio"
LEGACY_DIRS = ("live2d-master-agent", "Live2DMasterAgent")


class TestVaultKeyPathCompat:
    """密钥库路径：丢了它等于丢了用户存的所有上游 Key。"""

    def test_fresh_install_uses_new_directory(self, tmp_path):
        path = master_key_path_for_platform("win32", {"APPDATA": str(tmp_path), "HOME": str(tmp_path)})
        assert path.parent.name == NEW_DIR
        assert not path.exists()

    @pytest.mark.parametrize("legacy", LEGACY_DIRS)
    def test_existing_legacy_vault_is_reused_not_replaced(self, tmp_path, legacy):
        """旧目录里已有 vault.key 时必须继续用它。

        直接换新目录会让首次读取变成「密钥不存在 → 新建一把」，
        于是旧密文全部解不开 —— 那是数据损坏，不是改名。
        """
        old_dir = tmp_path / legacy
        old_dir.mkdir()
        (old_dir / "vault.key").write_text("existing-key", encoding="utf-8")

        path = master_key_path_for_platform("win32", {"APPDATA": str(tmp_path), "HOME": str(tmp_path)})

        assert path.parent.name == legacy

    def test_new_location_wins_when_both_exist(self, tmp_path):
        for name in (NEW_DIR, LEGACY_DIRS[0]):
            d = tmp_path / name
            d.mkdir()
            (d / "vault.key").write_text("k", encoding="utf-8")

        path = master_key_path_for_platform("win32", {"APPDATA": str(tmp_path), "HOME": str(tmp_path)})

        assert path.parent.name == NEW_DIR

    def test_darwin_and_linux_roots_are_honoured(self, tmp_path):
        mac = master_key_path_for_platform("darwin", {"HOME": str(tmp_path)})
        assert mac.parent.parent == tmp_path / "Library" / "Application Support"
        assert mac.parent.name == NEW_DIR

        linux = master_key_path_for_platform("linux", {"XDG_CONFIG_HOME": str(tmp_path)})
        assert linux.parent == tmp_path / NEW_DIR

    def test_explicit_override_still_wins(self, tmp_path):
        forced = tmp_path / "elsewhere.key"
        path = master_key_path_for_platform(
            "win32", {"APPDATA": str(tmp_path), "HOME": str(tmp_path), "LIVE2D_VAULT_KEY_FILE": str(forced)}
        )
        assert path == forced


class TestDataRootCompat:
    """数据/产物目录同规则：新名优先，旧名沿用。"""

    def test_fresh_home_uses_new_name(self, tmp_path):
        root = data_root_for_platform("win32", {"APPDATA": str(tmp_path), "HOME": str(tmp_path)})
        assert root.name == NEW_DIR

    @pytest.mark.parametrize("legacy", LEGACY_DIRS)
    def test_existing_legacy_data_dir_is_kept(self, tmp_path, legacy):
        old = tmp_path / legacy
        (old / "characters").mkdir(parents=True)

        root = data_root_for_platform("win32", {"APPDATA": str(tmp_path), "HOME": str(tmp_path)})

        assert root == old

    def test_env_override_wins(self, tmp_path):
        root = data_root_for_platform(
            "win32", {"APPDATA": str(tmp_path), "HOME": str(tmp_path), "LIVE2D_DATA_DIR": str(tmp_path / "custom")}
        )
        assert root == tmp_path / "custom"

    def test_api_server_and_optional_components_share_the_resolver(self, tmp_path, monkeypatch):
        """两处 Python 入口必须真的调用同一个解析器，且不能各自抛错。

        以前两边各自拼 `~/Live2DMasterAgent`，改名时很容易只改一边 ——
        结果是 Go 写 A 目录、Python 读 B 目录，用户看到的是「角色库空了」。
        """
        import api_server
        import core.app_paths as app_paths
        import scripts.optional_components as oc
        from importlib import reload

        sentinel = tmp_path / "resolved"
        calls = []

        def fake_resolve():
            calls.append(1)
            return sentinel

        monkeypatch.setattr(app_paths, "data_root", fake_resolve)

        assert api_server._data_root() == sentinel
        assert oc.data_root() == sentinel
        assert len(calls) == 2, "两个入口都应经统一解析器取路径"
