"""装配脚本的 wheel 解析必须钉死「随包分发的解释器」，而不是跟着宿主机走。

背景：安装包里的解释器是嵌入式 CPython 3.12（`PYTHON_VERSION`），但
`pip download/install` 默认按**宿主机**解释器挑轮子与环境标记。CI 恰好用
`setup-python: 3.12` 才没出事；在 3.14 的 Windows 机器上跑同一条命令，会解析出
cp314 的 numpy/opencv，还会把 `python_version < "3.14"` 判成 False 而装成
pygame-ce —— 全部灌进 3.12 的 site-packages，装完直接 ImportError。
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "build_desktop_bundle.py"


@pytest.fixture(scope="module")
def bundler():
    spec = importlib.util.spec_from_file_location("build_desktop_bundle", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("host_os", ["Windows", "Linux", "Darwin"])
def test_wheel_resolution_pins_the_bundled_interpreter(bundler, monkeypatch, host_os):
    """无论宿主是什么系统，轮子标签与环境标记都必须按 cp312 / win_amd64 求值。"""
    # 替换 bundler 命名空间里的 platform 引用，避免改动全局 stdlib 模块
    monkeypatch.setattr(bundler, "platform", types.SimpleNamespace(system=lambda: host_os))
    args = bundler.pip_common_args()

    assert "--only-binary=:all:" in args, "跨环境解析必须禁用源码分发"
    expected = {
        "--python-version": [bundler.PYTHON_VERSION[:4]],
        "--implementation": ["cp"],
        "--abi": [bundler.PYTHON_ABI],
        "--platform": ["win_amd64"],
    }
    for flag, want in expected.items():
        assert flag in args, f"{host_os} 宿主机上缺少 {flag}：{args}"
        values = [args[i + 1] for i, a in enumerate(args) if a == flag]
        assert values == want, f"{flag} 应为 {want}，实际 {values}"


def test_pinned_interpreter_matches_the_embedded_python(bundler):
    """钉的解释器必须就是解压出来的那个嵌入式 CPython。"""
    args = bundler.pip_common_args()
    version = args[args.index("--python-version") + 1]
    abi = args[args.index("--abi") + 1]
    assert version == bundler.PYTHON_VERSION[:4] == "3.12"
    assert abi == f"cp{version.replace('.', '')}" == bundler.PYTHON_ABI
    assert bundler.PYTHON_EMBED_URL.endswith(
        f"{bundler.PYTHON_VERSION}/python-{bundler.PYTHON_VERSION}-embed-amd64.zip"
    )


def _names(bundler, lines: list[str]) -> set[str]:
    return {bundler.Requirement(line).name.lower() for line in lines}


SMOKE_MANIFEST = """\
Pillow>=13.0.0
# 注释行不该进解析结果
pygame>=2.5.0; python_version < "3.14"
pygame-ce>=2.5.8; python_version >= "3.14"
onnxruntime-directml>=1.24.4; sys_platform == "win32"
onnxruntime>=1.31.0; sys_platform != "win32"
"""


def test_flatten_evaluates_markers_against_bundled_runtime_not_host(bundler):
    """标记必须按随包解释器（3.12 / win32）求值。

    pip 的 --python-version/--platform 只约束轮子标签，标记仍按宿主机求值
    （pip/_internal/req/req_install.py 用的是 packaging 的 default_environment）。
    不自己接管这一步，在 3.14 机器上装配就会把 pygame-ce 灌进 3.12 运行时。
    """
    lines = bundler.flatten_requirements(SMOKE_MANIFEST)
    names = _names(bundler, lines)
    assert names == {"pillow", "pygame", "onnxruntime-directml"}
    assert not any(";" in line for line in lines), f"求值后不应残留标记：{lines}"
    assert not any(line.startswith("#") for line in lines)


def test_flatten_keeps_pip_option_lines(bundler):
    """`-` 开头的选项行（--index-url 等）要原样透传给 pip。"""
    text = "--index-url https://example.invalid/simple\nPillow>=13.0.0\n"
    assert bundler.flatten_requirements(text) == [
        "--index-url https://example.invalid/simple",
        "Pillow>=13.0.0",
    ]


def test_flatten_rejects_unparsable_line(bundler):
    """解析不了的依赖行必须当场报，不能静默丢掉一条运行时依赖。"""
    with pytest.raises(ValueError, match="无法解析依赖行"):
        bundler.flatten_requirements("this is not a requirement @@@\n")


def test_real_desktop_manifest_resolves_to_the_shipped_runtime(bundler):
    """真清单按 3.12/win32 求值后的结果，就是安装包 site-packages 该有的样子。"""
    text = (PROJECT_ROOT / "requirements.desktop.txt").read_text(encoding="utf-8")
    names = _names(bundler, bundler.flatten_requirements(text))
    assert "pygame" in names and "pygame-ce" not in names
    assert "onnxruntime-directml" in names and "onnxruntime" not in names
    for unshipped in ("rembg", "torch", "torchvision", "transformers"):
        assert unshipped not in names, f"{unshipped} 不该随安装包分发"


@pytest.mark.parametrize("stage", ["download", "install"])
def test_pip_receives_the_resolved_manifest(bundler, tmp_path, monkeypatch, stage):
    """pip 必须拿到求值后的清单，而不是原始 `-r requirements.desktop.txt`。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(bundler, "run", lambda cmd: calls.append(cmd))
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    manifest = PROJECT_ROOT / "requirements.desktop.txt"
    if stage == "download":
        bundler.download_wheels(manifest, wheels_dir, False)
    else:
        monkeypatch.setattr(bundler, "dir_size", lambda p: 0)
        bundler.install_wheels(manifest, wheels_dir, tmp_path / "site-packages")

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == bundler.sys.executable and cmd[1:3] == ["-m", "pip"]
    assert cmd[3] == stage
    given = Path(cmd[cmd.index("-r") + 1])
    assert given.resolve() != manifest.resolve(), "不能把未求值的原始清单交给 pip"
    body = given.read_text(encoding="utf-8")
    assert "pygame-ce" not in body
    assert any(line.startswith("pygame>=") for line in body.splitlines())
    assert "onnxruntime-directml" in body


def test_wheel_cache_is_invalidated_by_the_resolution_result(bundler, tmp_path, monkeypatch):
    """缓存标记必须含解析结果：只钉清单原文的话，换解释器口径会复用旧 wheel 集。

    CI 的 actions/cache 用 restore-keys 前缀匹配，正是这个坑的现场（清单没改、
    解析口径改了，旧目录照样被恢复出来）。
    """
    manifest = PROJECT_ROOT / "requirements.desktop.txt"
    monkeypatch.setattr(bundler, "run", lambda cmd: None)
    wheels_dir = tmp_path / "wheels"
    bundler.download_wheels(manifest, wheels_dir, False)
    marker = wheels_dir / ".requirements.sha256"
    before = marker.read_text(encoding="utf-8").strip()

    # 3.14 会把 `python_version < "3.14"` 判反（pygame → pygame-ce），解析结果必变
    monkeypatch.setattr(bundler, "PYTHON_VERSION", "3.14.1")
    assert bundler._requirements_digest(manifest) != before, "解析口径变了却仍判定缓存可复用"



def test_smoke_lists_do_not_warn_for_deliberately_unshipped_packages(bundler):
    """刻意不进基础包的依赖不该留在冒烟清单里 —— 每次装配都会刷一条无意义 warn。

    与 torch 那一组同理（脚本注释已写明口径）：rembg 在 v0.10.10 移出基础包，
    改为应用内按需安装，全新 bundle 里必然缺失。
    """
    unshipped = {"torch", "torchvision", "transformers", "rembg"}
    shipped = set(bundler.SMOKE_REQUIRED) | set(bundler.SMOKE_OPTIONAL)
    assert not (shipped & unshipped), f"冒烟清单仍包含不随包分发的：{shipped & unshipped}"
