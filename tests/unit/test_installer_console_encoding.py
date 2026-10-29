"""一键安装器必须能在 Windows 默认 GBK 控制台下跑完。

v0.10.9 之前修过 api_server / lint_architecture 的同类崩溃，但 install.py 漏了：
它满屏 emoji（🔍 🎭 ✓）在 cp936 上会 UnicodeEncodeError，
用户在中文 Windows 上双击/命令行跑安装 → 还没开始装就 exit 1。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run(args, encoding_name="gbk"):
    env = dict(os.environ, PYTHONIOENCODING=encoding_name)
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=PROJECT_ROOT,
        timeout=120,
    )


def test_installer_dry_run_survives_gbk_console():
    """`--dry-run` 会走到打印步骤横幅的路径：必须不崩，且退出码不为编码错误。"""
    proc = _run(["install.py", "--dry-run", "--yes"])

    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "UnicodeEncodeError" not in combined, combined[-800:]
    assert proc.returncode in (0, 1), f"异常退出码 {proc.returncode}: {combined[-400:]}"
    # 真跑到了打印环节（不是提前退出造成「假通过」）
    assert "dry-run" in combined.lower() or "DRY" in combined, combined[-300:]


def test_installer_check_mode_survives_gbk_console():
    """--check 只做环境体检，不装东西，是用户最常先跑的一条。"""
    proc = _run(["install.py", "--check"])

    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "UnicodeEncodeError" not in combined, combined[-800:]
    assert proc.returncode in (0, 1), f"异常退出码 {proc.returncode}: {combined[-400:]}"


def test_utf8_console_still_works():
    """修复不能把正常 UTF-8 终端的中文输出弄成乱码问号。"""
    proc = _run(["install.py", "--dry-run", "--yes"], encoding_name="utf-8")

    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "UnicodeEncodeError" not in combined
    assert proc.returncode in (0, 1)
