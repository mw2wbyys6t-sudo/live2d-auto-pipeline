"""产品名一致性：Live2D Master Agent → Pygmalion Studio。

两条边界要分清：
- **在用的代码/配置/当前文档**必须统一改名，否则会留下「装出来的 exe 叫 A、
  进程查找找 B」这类断链。
- **历史记录**（已发布版本的 CHANGELOG 段落、ADR、日期快照文档、GitHub 上
  已存在的 v0.10.9 产物文件名）保持原样 —— 改写历史会让验收记录与真实产物对不上。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

DISPLAY = "Pygmalion Studio"
KEBAB = "pygmalion-studio"
COMPACT = "PygmalionStudio"
LEGACY_PATTERNS = re.compile(
    r"Live2D Master Agent|Live2DMasterAgent|live2d[-_]master[-_]agent", re.I
)

#: 历史记录：保留旧名是刻意的，不算漂移。
HISTORICAL = (
    "CHANGELOG.md",
    "README.md",
    "docs/architecture/adrs/",
    "docs/research/",
    "docs/superpowers/",
    "docs/reviews/",
    "docs/manual-test-checklist-v0.10.8.md",
    "docs/performance-report-2026-10-23.md",
    "docs/native-runtime-verification.md",
    "docs/PRD-desktop-v1.md",
    "tools/archive/",
)

#: 自身必须能提到旧名（否则测不出漏改/回退），因此排除在扫描之外。
SELF_REFERENTIAL_TESTS = {
    "tests/unit/test_product_name_consistency.py",
    "tests/unit/test_product_rename_compat.py",
}

#: 需要改名、且必须逐项核对的具体位置。
IDENTITY_FILES = {
    "desktop/tauri.conf.json",
    "desktop/Cargo.toml",
    "desktop/src/lib.rs",
    "desktop/dist/index.html",  # WebView 壳：窗口标题取自它的 <title>
    "deploy/installer/desktop.iss",
    ".github/workflows/tauri-build.yml",
    ".github/workflows/ci.yml",
    "api/main.go",
    "api/handlers/handlers.go",
}


def _live_text_files() -> list[Path]:
    skip_dirs = {".git", "node_modules", ".next", "target", "output", ".cache",
                 "bundle", "out"}
    suffixes = {".py", ".go", ".rs", ".ts", ".tsx", ".js", ".json", ".toml", ".yml",
                ".yaml", ".md", ".txt", ".iss", ".html", ".css", ".in"}
    out = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        rel = p.relative_to(ROOT).as_posix()
        # 生成物不算源码：web/out 由 next build 重刷，改名靠源头改 + 重新构建。
        # api/webui/dist 是 web/out 的整目录拷贝（Go 二进制内嵌的落点）。
        # 注意不能按「目录名叫 dist 就跳过」一刀切 —— desktop/dist/index.html
        # 是**入库且随安装包分发**的 WebView 壳（窗口标题就是它的 <title>），
        # 一刀切会把这类真正需要核对的文件一起挡掉。
        if rel.startswith(("web/out/", "api/webui/dist/")):
            continue
        if any(part in skip_dirs for part in p.parts):
            continue
        if rel.startswith(HISTORICAL) or rel in SELF_REFERENTIAL_TESTS:
            continue
        if "/archive/" in f"/{rel}":
            continue
        out.append(p)
    return out


def test_no_legacy_product_name_left_in_live_sources():
    """在用的源码/配置/当前文档里不应再出现旧产品名。

    刻意保留的回退路径（旧 .env 位置、旧数据目录常量）必须在同一行写明 legacy，
    这样这条测试才既挡得住漏改，也不会挡住必要的兼容代码。
    """
    offenders: list[str] = []
    for path in _live_text_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in SELF_REFERENTIAL_TESTS:
            continue  # 这些文件必须能提到旧名，否则测不了漏改与回退
        body = path.read_text(encoding="utf-8", errors="replace")
        for line in body.splitlines():
            if LEGACY_PATTERNS.search(line) and "legacy" not in line.lower() and "旧" not in line:
                offenders.append(f"{rel}: {line.strip()[:110]}")
                break
    assert not offenders, "仍有旧产品名未改（或回退行没标 legacy）：\n" + "\n".join(offenders[:15])


@pytest.mark.parametrize("rel", sorted(IDENTITY_FILES))
def test_identity_files_have_no_legacy_product_name(rel):
    body = (ROOT / rel).read_text(encoding="utf-8")
    leftovers = [line for line in body.splitlines() if LEGACY_PATTERNS.search(line)
                 and "legacy" not in line.lower() and "旧" not in line]
    assert not leftovers, f"{rel} 仍留着旧产品名：{leftovers[:3]}"


def test_tauri_identity_points_at_new_brand():
    conf = json.loads((ROOT / "desktop/tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["productName"] == DISPLAY
    assert conf["identifier"] == "com.pygmalion.studio"


def test_cargo_package_and_lib_names_follow_kebab():
    body = (ROOT / "desktop/Cargo.toml").read_text(encoding="utf-8")
    assert re.search(r'^name = "%s"$' % KEBAB, body, re.M), "Cargo 包名决定 exe 名，必须跟着改"
    assert f'name = "{KEBAB.replace("-", "_")}_lib"' in body, "lib 名要与包名同族"


def test_rust_still_finds_the_go_binary_name():
    """Go 后端二进制名 `live2d-api` 是内部标识，不随品牌改；改名会把启动链打断。"""
    body = (ROOT / "desktop/src/lib.rs").read_text(encoding="utf-8")
    assert '"live2d-api.exe"' in body and '"live2d-api"' in body
    assert COMPACT in body, "桌面 exe 查找名应已换成新品牌"


def test_installer_names_and_output_are_consistent():
    body = (ROOT / "deploy/installer/desktop.iss").read_text(encoding="utf-8")
    assert f'#define MyAppName      "{DISPLAY}"' in body
    assert f'#define MyAppExeName   "{COMPACT}.exe"' in body
    assert f"OutputBaseFilename={COMPACT}-Setup-" in body


def test_ci_artifact_paths_match_cargo_package_name():
    """CI 按 Cargo 包名找二进制；包名改了这里必须跟着改（此前就踩过按 productName 找的坑）。"""
    for rel in (".github/workflows/tauri-build.yml", ".github/workflows/ci.yml"):
        body = (ROOT / rel).read_text(encoding="utf-8")
        assert f"{KEBAB}.exe" in body, f"{rel} 仍在找旧包名的产物"


def test_brand_literal_has_a_single_frontend_source():
    """前端品牌字面量只能出现在 web/lib/brand.ts。

    写在组件里就会重演版本号那类事：改名时漏掉某处，界面上两个名字并存。
    """
    sources = [
        p for p in (ROOT / "web").rglob("*")
        if p.is_file() and p.suffix in {".ts", ".tsx", ".js"}
        # 构建产物里必然带品牌字面量，但它不是「第二处来源」。next.config.js
        # 允许用 NEXT_DIST_DIR 指到别的目录（如 .next-dev），所以按「点目录」
        # 整体排除，而不是写死 .next。
        and not any(part.startswith(".") or part in {"node_modules", "out"} for part in p.parts)
    ]
    holders = [
        p.relative_to(ROOT).as_posix()
        for p in sources
        if DISPLAY in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert holders == ["web/lib/brand.ts"], f"品牌字面量散落多处：{holders}"


def test_env_file_search_keeps_legacy_path_as_fallback():
    """外部 skill 安装目录是按旧仓库名落的盘，直接改名会让已配置的 .env 读不到。"""
    body = (ROOT / "core/config.py").read_text(encoding="utf-8")
    assert "skills" in body
    assert "live2d-master-agent" in body, "必须保留历史 .env 搜索路径作为回退"
    assert KEBAB in body, "同时要有新名的搜索路径"
