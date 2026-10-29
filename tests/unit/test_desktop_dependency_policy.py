"""桌面基础包 GPU 运行时的依赖策略测试（Windows 默认带 DirectML）。

背景（实测）：`onnxruntime` 与 `onnxruntime-directml` 是两个发行名、同一个
`onnxruntime/` 模块目录。谁后装谁的文件生效，pip 完全不知情；而 `rembg[cpu]`
钉着 `onnxruntime`。所以「Windows 默认带 GPU 运行时」必须由清单结构保证，
而不是靠安装顺序碰运气。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _requirements(path: Path) -> list[Requirement]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text and not text.startswith("-"):
            out.append(Requirement(text))
    return out


def _desktop() -> list[Requirement]:
    return _requirements(PROJECT_ROOT / "requirements.desktop.txt")


def _names(reqs: list[Requirement]) -> set[str]:
    return {r.name.lower() for r in reqs}


def test_windows_base_installs_directml_runtime():
    """Windows 的桌面基础包必须装 onnxruntime-directml（带 DmlExecutionProvider）。"""
    entries = [r for r in _desktop() if r.name.lower() == "onnxruntime-directml"]
    assert entries, "requirements.desktop.txt 必须包含 onnxruntime-directml"
    marker = str(entries[0].marker or "")
    assert "win32" in marker, f"DirectML 条目应限定 Windows，实际 marker={marker!r}"


def test_plain_onnxruntime_is_excluded_on_windows():
    """CPU 版 onnxruntime 不能出现在 Windows 解析结果里，否则会顶掉 DirectML。"""
    plain = [r for r in _desktop() if r.name.lower() == "onnxruntime"]
    assert plain, "非 Windows 平台仍需要 onnxruntime（DirectML 只发 Windows wheel）"
    marker = str(plain[0].marker or "")
    assert marker and "win32" in marker, f"必须带平台标记，实际 marker={marker!r}"
    # 在 win32 环境下这条必须被求值为 False
    env = {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
           "python_version": "3.12"}
    assert not plain[0].marker.evaluate(env), "onnxruntime 在 win32 下必须不生效"


@pytest.mark.parametrize("manifest", ["requirements.txt", "requirements.desktop.txt"])
def test_no_base_dependency_pulls_cpu_onnxruntime_on_windows(manifest):
    """rembg[cpu] 钉着 onnxruntime，会把 DirectML 文件静默覆盖 —— 它不属于基础包。

    它本来就是「应用内按需下载」的能力组（api/services/capability_manager.go 的
    capabilityPlans、core/capabilities.py 的设计契约都这么写）。
    """
    names = _names(_requirements(PROJECT_ROOT / manifest))
    assert "rembg" not in names, f"{manifest} 不应把 rembg 打进基础依赖"


def test_capability_installer_gpu_spec_matches_manifest():
    """Go 按需安装器里恢复 GPU 用的发行名，必须等于清单的 win32 条目。

    两处各写一份时，抬 DirectML 下限会只改一边：rembg 装完恢复出旧版本，
    或者干脆 pip 报错 —— 而界面只会显示「安装失败」。
    """
    go_src = (PROJECT_ROOT / "api" / "services" / "capability_manager.go").read_text(encoding="utf-8")
    match = re.search(r'const gpuRuntimeSpec = "([^"]+)"', go_src)
    assert match, "capability_manager.go 应定义 gpuRuntimeSpec"

    directml = [r for r in _desktop() if r.name.lower() == "onnxruntime-directml"]
    assert len(directml) == 1
    assert f"{directml[0].name}{directml[0].specifier}" == match.group(1)


def test_ci_installs_declared_floors():
    """ci.yml 声称「按声明的下限安装」，那它写的数字就必须等于清单里的下限。

    上一轮抬了下限却只改了清单，这条测试就是防止那种漂移再次发生。
    """
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"pip install \"Pillow.*?\n(?:.*\\\n.*?)*?\"onnxruntime[^\"\n]*\"", ci, re.S)
    assert match, "未找到 ci.yml 的下限安装片段"
    pinned = dict(re.findall(r'"([A-Za-z0-9_.-]+)([<>=!~]+[^"]*)"', match.group(0)))
    floors = {r.name.lower(): str(r.specifier) for r in _requirements(PROJECT_ROOT / "requirements.txt")}
    for name, spec in pinned.items():
        key = name.lower()
        if key in ("opencv-python",):  # CI 与清单同名，直接比
            pass
        assert key in floors, f"ci.yml 装了清单里没有的 {name}"
        assert floors[key] == spec, f"{name}: ci.yml={spec} 但 requirements.txt={floors[key]}"
