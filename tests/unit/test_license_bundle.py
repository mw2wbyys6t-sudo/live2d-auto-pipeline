"""FF-15「License 与署名完整性」的许可打包契约。

背景：ADR-001 (Rev.4) 要求**对外分发的产出物**内含与仓库根 ``LICENSE``
逐字节一致的 Apache-2.0 许可全文（根目录若有 ``NOTICE`` 则一并保留）。
早先这条只是纸面承诺 —— 产出的模型包被用户再分发时不带任何许可声明。

这些断言锁住的是「产出物里有没有许可、是不是同一份」这条契约，
不关心具体是哪个后端（Python 降级服务 / Go 导出桥）产出的。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.license_bundle import (  # noqa: E402
    install_license_bundle,
    license_files,
    repo_root,
)

import api_server  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    with TestClient(api_server.app) as c:
        yield c


class TestLicenseBundle:
    def test_repo_root_is_found_by_license_file(self):
        """仓库根的定位以 LICENSE 文件的存在为准（不靠相对层级猜）。"""
        root = repo_root()
        assert (root / "LICENSE").is_file(), f"仓库根应有 LICENSE，实际 {root}"

    def test_license_files_contains_root_license(self):
        names = [p.name for p in license_files()]
        assert any(n.startswith("LICENSE") or n.startswith("COPYING") for n in names), (
            f"许可文件清单里没有 LICENSE：{names}")

    def test_install_copies_license_byte_identical(self, tmp_path):
        written = install_license_bundle(tmp_path)
        assert written, "应至少复制出 LICENSE"
        for name in written:
            src = repo_root() / name
            dst = tmp_path / name
            assert dst.is_file(), f"{name} 未被复制进产出目录"
            assert dst.read_bytes() == src.read_bytes(), (
                f"{name} 与仓库根不逐字节一致 —— FF-15 要求完全相同")

    def test_install_creates_missing_target_dir(self, tmp_path):
        target = tmp_path / "nested" / "export_dir"
        written = install_license_bundle(target)
        assert written
        assert (target / written[0]).is_file()

    def test_install_is_idempotent(self, tmp_path):
        """重复调用不应报错，也不应改变内容（导出重试是常态）。"""
        first = install_license_bundle(tmp_path)
        snapshot = {name: (tmp_path / name).read_bytes() for name in first}
        second = install_license_bundle(tmp_path)
        assert first == second
        for name, data in snapshot.items():
            assert (tmp_path / name).read_bytes() == data

    def test_license_is_apache_2_0(self):
        """许可文本必须真的是 Apache-2.0（不是占位或别的协议）。"""
        text = (repo_root() / "LICENSE").read_text(encoding="utf-8", errors="ignore")
        assert "Apache License" in text[:200]
        assert "Version 2.0" in text[:400]


class TestExportPackageCarriesLicense:
    """导出端点产出的模型目录必须带许可文件（用户会把它再分发出去）。"""

    def test_export_dir_contains_license(self, client, tmp_path, monkeypatch):
        from PIL import Image

        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.setattr(api_server, "OUTPUT_DIR", out)

        layers = out / "layers_x"
        layers.mkdir()
        for name in ("hair_back", "face"):
            Image.new("RGBA", (64, 64), (200, 120, 90, 255)).save(layers / f"{name}.png")

        r = client.post(
            "/api/export/live2d",
            json={"character_id": "lic_test", "layers_dir": str(layers)},
        )
        assert r.status_code == 200
        data = r.json()["data"]
        export_dir = Path(data["output_dir"])
        assert export_dir.is_dir()
        license_file = export_dir / "LICENSE"
        assert license_file.is_file(), (
            "导出目录缺少 LICENSE —— FF-15：产出物必须内含与仓库根逐字节一致的许可全文")
        assert license_file.read_bytes() == (repo_root() / "LICENSE").read_bytes()
