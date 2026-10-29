"""发布版本一致性：版本号只能有一个真源，其余位置必须跟着它走。

v0.10.8.1 / v0.10.9 都出过「硬编码漂移」事故（安装器横幅与 Cargo/tauri 版本不一致，
导致产物名与清单对不上）。这里把各处声明逐条对撞。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 本轮正式版本（新增 GPU 探测 + 自动选卡 + Windows 默认 DirectML 运行时）。
RELEASE_VERSION = "0.10.10"


def _read(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def test_version_single_source_declares_release_version():
    body = _read("core/version.py")
    assert re.search(rf'^__version__ = "{RELEASE_VERSION}"$', body, re.M), \
        "core/version.py 是版本号唯一真源"


def test_python_backend_version_matches_single_source():
    body = _read("api_server.py")
    match = re.search(r'^VERSION = "v([0-9.]+)-py"', body, re.M)
    assert match, "api_server.py 应声明与真源同版本的后端标识"
    assert match.group(1) == RELEASE_VERSION


def test_go_backend_version_matches_single_source():
    """Go 后端也有一处硬编码版本（诊断页会把它当 detail 显示），同样会漂移。"""
    body = _read("api/handlers/handlers.go")
    match = re.search(r'const Version = "v([0-9.]+)-go"', body)
    assert match, "api/handlers/handlers.go 应声明后端版本常量"
    assert match.group(1) == RELEASE_VERSION, "Go 后端版本与 core/version.py 漂移"


def test_desktop_manifests_match_single_source():
    cargo = _read("desktop/Cargo.toml")
    assert re.search(rf'^version = "{RELEASE_VERSION}"$', cargo, re.M), "desktop/Cargo.toml 版本漂移"
    tauri = json.loads(_read("desktop/tauri.conf.json"))
    assert tauri["version"] == RELEASE_VERSION, "desktop/tauri.conf.json 版本漂移"
    # Cargo.lock 里本项目自身那条也必须跟着走：v0.10.9 期间它一直停在 0.10.8，
    # 结果 `cargo build --locked` 直接失败（锁文件与清单不同步不是「警告」，是编不出来）。
    lock = _read("desktop/Cargo.lock")
    match = re.search(r'^name = "pygmalion-studio"\nversion = "([^"]+)"$', lock, re.M)
    assert match, "Cargo.lock 缺少 pygmalion-studio 条目"
    assert match.group(1) == RELEASE_VERSION, \
        f"Cargo.lock 停在 {match.group(1)}，与 Cargo.toml 的 {RELEASE_VERSION} 不同步"


def test_web_package_and_installer_match_single_source():
    pkg = json.loads(_read("web/package.json"))
    assert pkg["version"] == RELEASE_VERSION, "web/package.json 版本漂移"
    body = _read("install.py")
    assert f"v{RELEASE_VERSION}" in body, "install.py 横幅版本漂移"


def test_web_ui_does_not_hardcode_version():
    """UI 上显示的版本曾三处写死 `v0.10.9`，改版本时静默滞后（页脚显示旧版本）。

    版本必须由 next.config 从 web/package.json 注入（该文件已被上面的相等性测试
    钉在 core/version.py 上）。注释里的历史标记（`// v0.10.1: ...`）与
    `workflow_v0.10.2` 这类**协议字面量**不在本判据内 —— 它们是历史记录/线上契约。
    """
    hardcoded = re.compile(r"v\d+\.\d+\.\d+")

    def displayed_lines(rel: str):
        for i, line in enumerate(_read(rel).splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*")):
                continue
            if "workflow_v" in stripped:      # 后端协议字段，不是界面版本
                continue
            if "v0.10.1:" in stripped or "v0.10.2:" in stripped:
                continue                     # 变更注释
            if hardcoded.search(stripped):
                yield f"{rel}:{i}: {stripped[:80]}"

    offenders = [ln for rel in ("web/pages/index.tsx", "web/components/Sidebar.tsx")
                 for ln in displayed_lines(rel)]
    offenders += list(displayed_lines("web/lib/api-client.ts"))
    assert not offenders, "界面仍写死版本号：\n" + "\n".join(offenders)

    config = _read("web/next.config.js")
    assert "NEXT_PUBLIC_APP_VERSION" in config, "next.config 需注入版本常量"
    assert "package.json" in config, "注入值必须取自 web/package.json"


def test_changelog_top_entry_is_release_version():
    body = _read("CHANGELOG.md")
    first = re.search(r"^## \[v([0-9.]+)\]", body, re.M)
    assert first, "CHANGELOG 顶部应有版本条目"
    assert first.group(1) == RELEASE_VERSION, "CHANGELOG 顶部条目必须就是本轮发布版本"
