"""产出物的许可证打包（FF-15「License 与署名完整性」）。

ADR-001 (Rev.5) 定的义务：任何**对外分发**的产出物都必须内含 AGPL-3.0
许可全文，且与仓库根的 ``LICENSE`` **逐字节一致**；根目录若存在 ``NOTICE``
则一并保留。

为什么单独一个模块：许可证文件在仓库根，而产出物在 ``output/`` 或安装目录里，
两边路径毫无关系；而且**两条导出链路都要用**——

- Python 降级服务（``api_server.py``）直接建包；
- Go 服务的导出桥（``api/services/python_bridge.go`` 的
  ``bridgeExportLive2DScript``）把构建委托给 Python，但脚本本身是另一份
  Python 代码。

早先两条链路各自漏掉这件事，于是产出的模型包被用户再分发时**不带任何许可
声明** —— 这正是 FF-15 说的「纸面承诺」。本模块是唯一真源：两边都调它，
校验脚本 ``scripts/check-license-bundle.sh`` 验的也是同一份字节。

放在 ``core/``（最底层）而不是服务层：它只做「读两个文件 + 复制」，不依赖
任何上层，也不反向 import 服务层（符合 FF-1 规则 4）。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

__all__ = ["license_files", "repo_root", "install_license_bundle"]

#: 必须随产出物分发的文件。NOTICE 沿自 Apache-2.0 §4(d) 的条件义务，
#: 现行 AGPL-3.0（ADR-001 Rev.5）下沿用同一规则：
#: 仓库里没有就不强求，但一旦有了就必须一起带走。
_LICENSE_NAMES = ("LICENSE", "LICENSE.txt", "COPYING")
_NOTICE_NAMES = ("NOTICE", "NOTICE.txt")


def repo_root() -> Path:
    """仓库根目录（以 ``LICENSE`` 文件的存在为准，从本模块向上找）。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if any((parent / name).is_file() for name in _LICENSE_NAMES):
            return parent
    # 兜底：core/ 的上一级就是仓库根（正常布局下 LICENSE 一定在）。
    return here.parents[1]


def license_files() -> List[Path]:
    """仓库根里必须随产出物分发的许可文件（LICENSE + 存在的 NOTICE）。"""
    root = repo_root()
    found: List[Path] = []
    for name in _LICENSE_NAMES:
        candidate = root / name
        if candidate.is_file():
            found.append(candidate)
            break
    for name in _NOTICE_NAMES:
        candidate = root / name
        if candidate.is_file():
            found.append(candidate)
            break
    return found


def install_license_bundle(target_dir: Path) -> List[str]:
    """把许可证文件复制进产出目录，返回复制成功的文件名列表。

    复制而非软链：产出物会被复制到别的机器，软链会断。
    逐字节复制，保证与根目录 ``LICENSE`` 完全一致（FF-15 的验收判据）。

    Args:
        target_dir: 产出目录（如导出模型目录）。不存在时会被创建。

    Returns:
        实际写入的文件名列表；仓库里找不到任何许可文件时返回空列表
        （此时调用方应视为异常 —— 没有许可证可分发）。
    """
    target = Path(target_dir)
    written: List[str] = []
    for src in license_files():
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target / src.name)
            written.append(src.name)
        except OSError:
            # 复制失败不能静默：产出物缺许可证是合规问题，由调用方决定是否阻断。
            continue
    return written
