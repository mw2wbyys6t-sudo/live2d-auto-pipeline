"""随包 `app/` 树必须是**纯生成物**：不能留下源码里已经不存在的文件。

背景：`copy_app_tree` 用 `shutil.copytree(..., dirs_exist_ok=True)` 复制，
只合并、不清空。于是本机跑过一次内核后留下的 `__pycache__`、以及源码里
早已删除/改名的模块，都会**一路留在打包目录里进安装包** —— 用户拿到的
「新版本」里混着开发期的旧文件。仓库里 `output/`、`Work/` 的测试产物本身
不进包（`tauri.conf.json` 只打 `bundle/`），但这条合并语义会把旧构建的
残留带进去，是同一类「测试数据留在软件里」的问题。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "build_desktop_bundle.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_desktop_bundle", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_copy_app_tree_leaves_no_stale_source_files(tmp_path):
    """源码里已不存在的模块，不该出现在打包出来的 app/ 里。"""
    mod = _load()
    app_dir = tmp_path / "app"
    stale = app_dir / "core" / "module_removed_in_a_earlier_build.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("# 上一次构建留下的文件\n", encoding="utf-8")

    mod.copy_app_tree(app_dir)

    assert not stale.exists(), (
        "app/ 是纯生成物：copy_app_tree 必须先清空目标再复制，"
        "否则旧构建的模块会随包分发")
    # 真文件仍在，确认不是「什么都没复制」造成的假通过
    assert (app_dir / "core" / "workflow.py").is_file()


def test_copy_app_tree_leaves_no_bytecode(tmp_path):
    """`.pyc` / `__pycache__` 不进包：它们是本机跑过的痕迹，不是分发内容。"""
    mod = _load()
    app_dir = tmp_path / "app"
    pycache = app_dir / "core" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "config.cpython-312.pyc").write_bytes(b"\x00\r\r\n")

    mod.copy_app_tree(app_dir)

    leftovers = sorted(p.name for p in (app_dir / "core").rglob("__pycache__"))
    assert leftovers == [], f"app/core 下仍有字节码缓存目录：{leftovers}"
    assert (app_dir / "core" / "config.py").is_file()
