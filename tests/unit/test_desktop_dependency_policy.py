"""桌面基础包 GPU 运行时的依赖策略测试（Windows 默认带 DirectML）。

背景（实测）：`onnxruntime` 与 `onnxruntime-directml` 是两个发行名、同一个
`onnxruntime/` 模块目录。谁后装谁的文件生效，pip 完全不知情；而 `rembg[cpu]`
钉着 `onnxruntime`。所以「Windows 默认带 GPU 运行时」必须由清单结构保证，
而不是靠安装顺序碰运气。
"""

from __future__ import annotations

import contextlib
import re
import signal
from pathlib import Path

import pytest
from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 「按下限安装」那一段的起始标记。取 Pillow 是因为它是清单里第一条。
_CI_FLOOR_ANCHOR = 'pip install "Pillow'


@contextlib.contextmanager
def _time_limit(seconds: float):
    """给纯 CPU 调用加硬上限：正则回溯不会自己让出 GIL，只能靠 SIGALRM。"""

    def _raise(_signum, _frame):
        raise AssertionError(f"超过 {seconds}s 未完成（疑似正则灾难性回溯）")

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _ci_floor_installs(ci: str) -> dict[str, str]:
    """取出 ci.yml 里「按下限安装」那一段的 ``包名 -> 版本约束``。

    按**行**解析，不用跨行正则。Workflow 里到处是反斜杠续行，而
    ``.*?\\n(?:.*\\\\\\n.*?)*?`` 这类嵌套量词在 ``re.S`` 下会让候选切分点
    随续行数量指数增长 —— 实测只多一个带续行的 step，就把整份测试套件挂死
    在 33%。这里的成本与行数成线性。
    """
    lines = ci.splitlines()
    start = next((index for index, line in enumerate(lines)
                  if _CI_FLOOR_ANCHOR in line), None)
    assert start is not None, "未找到 ci.yml 的下限安装片段"

    block = [lines[start]]
    index = start
    while block[-1].rstrip().endswith("\\"):
        index += 1
        if index >= len(lines):
            break
        block.append(lines[index])

    joined = " ".join(line.rstrip().rstrip("\\") for line in block)
    return dict(re.findall(r'"([A-Za-z0-9_.-]+)([<>=!~]+[^"\s]*)"', joined))


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


def test_ci_floor_extraction_survives_many_backslash_continuations():
    """提取 CI 下限不能依赖跨行可回溯正则。

    旧实现是一条
    ``pip install "Pillow.*?\\n(?:.*\\\\\\n.*?)*?"onnxruntime..."`` 配合 ``re.S``：
    ``.*`` 能跨行，嵌套量词让候选切分点随文件里「反斜杠续行」的数量指数增长。
    实测 ci.yml 只多了一个带续行的 step，这条正则就从毫秒级变成挂死 1 小时以上
    （整份测试套件因此卡在 33%）。

    这里用 40 行连续反斜杠续行做压力输入，要求提取在 2 秒内完成且结果正确。
    """
    ci = (
        'name: demo\n'
        'steps:\n'
        '  - run: |\n'
        '      pip install "Pillow>=13.0.0" "numpy>=2.4.6" \\\n'
        '                "onnxruntime>=1.31.0"\n'
        '      # 下面这些「别的 step」正是旧正则的回溯燃料\n'
        '  - name: other\n'
        '    run: |\n'
        + "".join(f"      echo step{i} \\\n" for i in range(40))
        + "      echo done\n"
    )

    with _time_limit(2):
        floors = _ci_floor_installs(ci)

    assert floors == {"Pillow": ">=13.0.0", "numpy": ">=2.4.6",
                      "onnxruntime": ">=1.31.0"}


def test_ci_installs_declared_floors():
    """ci.yml 声称「按声明的下限安装」，那它写的数字就必须等于清单里的下限。

    上一轮抬了下限却只改了清单，这条测试就是防止那种漂移再次发生。
    """
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    pinned = _ci_floor_installs(ci)
    assert "onnxruntime" in {name.lower() for name in pinned}, \
        f"ci.yml 的下限安装片段应包含 onnxruntime，实际={sorted(pinned)}"

    floors = {r.name.lower(): str(r.specifier) for r in _requirements(PROJECT_ROOT / "requirements.txt")}
    for name, spec in pinned.items():
        key = name.lower()
        assert key in floors, f"ci.yml 装了清单里没有的 {name}"
        assert floors[key] == spec, f"{name}: ci.yml={spec} 但 requirements.txt={floors[key]}"
