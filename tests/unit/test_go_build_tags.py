"""Go 构建标签 ``-tags nomsgpack`` 的契约。

背景：gin 的 ``binding`` 默认内置 msgpack 编解码器（``ugorji/go/codec``），
它会连带拉进 ``net/rpc`` / ``encoding/gob`` / ``encoding/base32``，而且编译期
内存占用很大（在本仓库的构建环境里，编译它曾直接把 go 进程 OOM kill）。
本项目零 msgpack 使用 —— Go 代码与前端都查过 —— 所以在所有构建入口统一带
``-tags nomsgpack`` 把它裁掉。

这里锁住两条契约：

1. **Go 源码里不得出现 msgpack**。一旦有人用了 ``binding.MsgPack``，源码看起来
   就"支持 msgpack"，但带该标签的构建会在运行时拒绝这类请求 —— 属于最难查的
   一类不一致（代码没说谎，构建配置在说谎）。
2. **所有产出二进制的 ``go build`` 都必须带 ``-tags nomsgpack``**。漏掉任何一处，
   就会让开发构建与发布构建的编解码能力不一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
API_DIR = PROJECT_ROOT / "api"

#: 所有会产出 Go 二进制的构建入口。
BUILD_ENTRIES = [
    ".github/workflows/ci.yml",
    ".github/workflows/tauri-build.yml",
    "scripts/build_desktop.bat",
    "scripts/build_desktop.sh",
    "scripts/build_tauri_release.bat",
    "scripts/build_tauri_release.sh",
    "scripts/run_tauri_dev.bat",
    "scripts/run_tauri_dev.sh",
    "api/start.sh",
]


class TestNoMsgpackInSource:
    """源码零 msgpack —— 否则 nomsgpack 标签会让运行时静默拒绝。"""

    def test_go_sources_do_not_reference_msgpack(self):
        offenders = []
        for path in API_DIR.rglob("*.go"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            # 忽略大小写：binding.MsgPack / application/msgpack / msgpack 都算
            if "msgpack" in text.lower():
                for lineno, line in enumerate(text.splitlines(), 1):
                    if "msgpack" in line.lower():
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()}"
                        )
        assert not offenders, (
            "Go 源码里出现了 msgpack 引用，但构建统一带 -tags nomsgpack，"
            "运行时会拒绝 msgpack 请求：\n  " + "\n  ".join(offenders)
        )

    def test_frontend_does_not_send_msgpack(self):
        """前端若发 msgpack 请求，后端带 nomsgpack 后会直接不认。"""
        offenders = []
        web = PROJECT_ROOT / "web"
        for pattern in ("*.ts", "*.tsx", "*.js", "*.mjs"):
            for path in web.rglob(pattern):
                if "node_modules" in path.parts or ".next" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "msgpack" in text.lower():
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))
        assert not offenders, "前端出现 msgpack 引用：\n  " + "\n  ".join(offenders)


class TestBuildEntriesUseTags:
    """每个产出二进制的 go build 都必须带 -tags nomsgpack。"""

    @pytest.mark.parametrize("entry", BUILD_ENTRIES)
    def test_build_command_has_nomsgpack_tag(self, entry: str):
        path = PROJECT_ROOT / entry
        assert path.is_file(), f"构建入口不存在：{entry}"
        text = path.read_text(encoding="utf-8", errors="ignore")

        missing = []
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # 跳过注释（yml/sh/bat 都是 # 或 rem）
            if stripped.startswith("#") or stripped.lower().startswith("rem "):
                continue
            if "go build" not in stripped:
                continue
            # 只关心产出二进制的命令（带 -o）
            if not re.search(r"(^|\s)-o(\s|=)", stripped):
                continue
            if "-tags nomsgpack" not in stripped:
                missing.append(f"{entry}:{lineno}: {stripped}")

        assert not missing, (
            "以下 go build 产出二进制但缺少 -tags nomsgpack，"
            "会让开发构建与发布构建不一致：\n  " + "\n  ".join(missing)
        )
