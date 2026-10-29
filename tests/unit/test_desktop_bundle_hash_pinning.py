"""装配期 SHA256 锁定表：每个「未固定」的下载物都必须真的被实测记录。

背景：`scripts/windows/hashes.json` 约定「空字符串 = 首次构建实测后回填」，
而脚本结尾会把 `missing` 里的键连同 `record[key]` 一起打印出来供回填。
只要某个键只出现在提示清单里、没有对应的 `verify_hash` 调用，那行提示就会永远
打印空串 —— `onnxruntime-dll` 曾经就是这样：DLL 从 zip 解出来后直接拷进包，
从未被测过哈希，回填路径形同虚设。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "build_desktop_bundle.py"
HASHES_FILE = PROJECT_ROOT / "scripts" / "windows" / "hashes.json"


@pytest.fixture(scope="module")
def bundler():
    spec = importlib.util.spec_from_file_location("build_desktop_bundle", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"))


def _recorded_keys(tree: ast.Module) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "verify_hash" and len(node.args) >= 3):
            key = node.args[2]
            assert isinstance(key, ast.Constant), "锁定表键必须是字面量，便于静态核对"
            keys.add(key.value)
    return keys


def _prompted_keys(tree: ast.Module) -> set[str]:
    """取出结尾提示用的 `missing = [k for k in (...)]` 那个元组。"""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "missing"):
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Tuple) and all(
                    isinstance(item, ast.Constant) and isinstance(item.value, str)
                    for item in sub.elts):
                return {item.value for item in sub.elts}
    raise AssertionError("未找到结尾的 missing 提示清单")


def test_every_prompted_key_is_actually_measured():
    """提示回填的键集合必须与实测记录的键集合完全一致（双向）。"""
    tree = _tree()
    assert _prompted_keys(tree) == _recorded_keys(tree)


def test_pinned_keys_cover_every_recorded_download():
    """锁定表的键与实测点同集合：新增下载物必须两边一起加，缺一边即失败。"""
    table = json.loads(HASHES_FILE.read_text(encoding="utf-8"))
    assert _recorded_keys(_tree()) == {k for k in table if not k.startswith("_")}


def test_unpinned_hash_is_recorded_instead_of_failing(bundler, tmp_path, capsys):
    """表里留空 = 首次构建：记录实测值，不阻断。"""
    artifact = tmp_path / "onnxruntime.dll"
    artifact.write_bytes(b"first-build")
    record: dict = {}

    bundler.verify_hash(artifact, "", "onnxruntime-dll", record)

    assert record["onnxruntime-dll"] == hashlib.sha256(b"first-build").hexdigest()
    assert "未在锁定表中设置" in capsys.readouterr().out


def test_mismatch_evicts_the_source_cache_too(bundler, tmp_path):
    """只删解压产物会从同一个坏 zip 再解一次，所以来源缓存必须一起清掉。"""
    source = tmp_path / "onnxruntime-win-x64-1.31.0.zip"
    source.write_bytes(b"bad-zip")
    unpacked = tmp_path / "ort-1.31.0"
    dll = unpacked / "lib" / "onnxruntime.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"wrong-dll")
    record: dict = {}

    with pytest.raises(SystemExit) as exc:
        bundler.verify_hash(dll, "0" * 64, "onnxruntime-dll", record, evict=source)

    message = str(exc.value.code)
    assert "onnxruntime-dll" in message
    assert "0" * 64 in message
    assert not dll.exists()
    assert not source.exists()
    # 记录的是实测值，便于直接回填锁定表
    assert record["onnxruntime-dll"] == hashlib.sha256(b"wrong-dll").hexdigest()
