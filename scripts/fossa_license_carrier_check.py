#!/usr/bin/env python3
"""核对 FOSSA 报的许可证 ID 在本项目依赖里**到底有没有载体**。

FOSSA 的 License Compliance 报的是「issue」，不是「我们违反了许可」：
它把依赖树里出现的许可证文本（含依赖**内部捆绑**的第三方库许可证全文）逐条
记下来，再按后台策略分档。要在后台逐条 ignore，必须先知道每条 ID 的载体是
什么 —— 否则只能靠猜。

本脚本给出这个证据，并且**可复现**：

- 读 ``requirements.txt`` 得到本项目真实依赖清单（不扫整个 site-packages）；
- 对每个已安装发行包，读取其 ``License-Expression`` / ``Classifier``（元数据声明）；
- 在包目录里按 SPDX 许可证的**特征文本**找出许可证全文载体，并分类：

  - ``runtime``  载体在会被 import 的包里 → 随产物分发，要进合规评估；
  - ``docs`` / ``tests`` 载体只在文档或测试夹具里 → 不进产物、不进 import；
  - ``none``     该 SPDX ID **全库无载体** → 扫描器误判（编号错位/跨段拼接）；
  - ``metadata`` 发行包元数据自己声明了该许可。

用法：
    python scripts/fossa_license_carrier_check.py            # 人类可读报告
    python scripts/fossa_license_carrier_check.py --json     # 机器可读
    python scripts/fossa_license_carrier_check.py --expect numpy:GPL-1.0

``--expect`` 形如 ``<发行包>:<SPDX ID>``，用于逐条核对；``--fail-on`` 决定何时
以非零退出（默认只报告不拦截）。CI 里对「已判定为误判的 ID」用
``--fail-on runtime``：它一旦出现运行时载体，说明依赖结构变了，原来的误判
结论作废，必须重新复核。

为什么不做「自动 ignore」：FOSSA 的逐条 ignore 与自动规则是后台能力（Free
计划不可用），仓库侧只能先把事实固化成可复核的证据，见
``docs/license-policy.md`` §3.2。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: 会被跳过的文件后缀：二进制与编译产物里不会有许可证全文。
_SKIP_SUFFIX = {".so", ".pyd", ".dll", ".a", ".pyc", ".npy", ".npz", ".png",
                ".jpg", ".jpeg", ".gif", ".webp", ".whl", ".zip", ".tar", ".gz"}

#: 单文件读取上限（字节）：许可证全文都在几十 KB 内，字体二进制可能更大。
_MAX_FILE_BYTES = 3_000_000


@dataclass(frozen=True)
class LicenseTemplate:
    """一个 SPDX ID 的识别特征。"""

    spdx_id: str
    patterns: Tuple[str, ...]

    def matches(self, text: str) -> bool:
        return any(re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                   for pattern in self.patterns)


#: 已核实的特征文本。刻意**不用** SPDX id 字符串本身去搜 —— 扫描器的 ID 往往
#: 来自跨段拼接，用 id 搜只会把误判再复现一遍。
TEMPLATES: Tuple[LicenseTemplate, ...] = (
    LicenseTemplate("GPL-1.0", (r"GNU GENERAL PUBLIC LICENSE\s+Version 1",
                                  r"Version 1, February 1989")),
    LicenseTemplate("GPL-2.0", (r"GNU GENERAL PUBLIC LICENSE\s+Version 2",
                                  r"Version 2, June 1991")),
    LicenseTemplate("GPL-3.0", (r"GNU GENERAL PUBLIC LICENSE\s+Version 3",
                                  r"Version 3, 29 June 2007")),
    LicenseTemplate("LGPL-2.0", (r"GNU LIBRARY GENERAL PUBLIC LICENSE\s+Version 2",
                                  r"GNU Library General Public License\s+.*Version 2,")),
    LicenseTemplate("LGPL-2.1", (r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 2\.1",
                                  r"GNU Library General Public License\s+.*version 2\.1")),
    LicenseTemplate("LGPL-3.0", (r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 3",
                                  r"Version 3, 29 June 2007.*GNU LESSER")),
    LicenseTemplate("Apache-2.0 WITH LLVM-exception",
                    (r"with LLVM Exceptions", r"Apache License v2\.0 with LLVM")),
    LicenseTemplate("Bitstream Vera", (r"Bitstream Vera",)),
)

#: 载体的性质分类：路径里出现这些片段就不是运行时代码。
_DOC_MARKERS = ("/docs/", "/doc/", "/documentation/")
_TEST_MARKERS = ("/tests/", "/test/", "/fixtures/", "/testdata/")

#: FOSSA 报的 ID → 本脚本的规范 ID。FOSSA 的 ID 带 -only/-or-later/-later 与
#: 变体写法（``GPL-3.0-with-GCC-exception``、``apache-2.0 WITH lm-exception``、
#: ``Bitstreamream``），但**许可证全文只有一份**，所以先归一到规范 ID 再找载体。
SPDX_ALIASES: Dict[str, str] = {
    "gpl-1.0": "GPL-1.0",
    "gpl-2.0": "GPL-2.0", "gpl-2.0-only": "GPL-2.0", "gpl-2.0-later": "GPL-2.0",
    "gpl-2.0-or-later": "GPL-2.0",
    "gpl-3.0": "GPL-3.0", "gpl-3.0-only": "GPL-3.0",
    "gpl-3.0-or-later": "GPL-3.0",
    "gpl-3.0-with-gcc-exception": "GPL-3.0",
    "gpl-3.0-with-gcc-exception-3.1": "GPL-3.0",
    "lgpl-2.0": "LGPL-2.0", "lgpl-2.0-only": "LGPL-2.0",
    "lgpl-2.0-or-later": "LGPL-2.0",
    "lgpl-2.1": "LGPL-2.1", "lgpl-2.1-only": "LGPL-2.1",
    "lgpl-2.1-or-later": "LGPL-2.1", "lgpl-2.1-later": "LGPL-2.1",
    "lgpl-3.0": "LGPL-3.0", "lgpl-3.0-only": "LGPL-3.0",
    "lgpl-3.0-or-later": "LGPL-3.0",
    "apache-2.0 with lm-exception": "Apache-2.0 WITH LLVM-exception",
    "apache-2.0 with llvm-exception": "Apache-2.0 WITH LLVM-exception",
    "bitstreamream": "Bitstream Vera", "bitstream vera": "Bitstream Vera",
}


def canonical_spdx(spdx_id: str) -> str:
    """把 FOSSA 的 ID 归一到本脚本使用的规范 ID。"""
    return SPDX_ALIASES.get(spdx_id.strip().lower(), spdx_id.strip())


@dataclass
class Carrier:
    """一条许可证全文载体。"""

    package: str
    spdx_id: str
    path: str
    kind: str = "runtime"          # runtime | docs | tests
    excerpt: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {"package": self.package, "spdx_id": self.spdx_id,
                "path": self.path, "kind": self.kind, "excerpt": self.excerpt}


@dataclass
class DistributionFinding:
    """一个发行包的核对结果。"""

    name: str
    version: str = ""
    metadata_licenses: List[str] = field(default_factory=list)
    carriers: List[Carrier] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "version": self.version,
                "metadata_licenses": self.metadata_licenses,
                "carriers": [item.as_dict() for item in self.carriers]}


# ----------------------------------------------------------------------
# 依赖清单
# ----------------------------------------------------------------------

def requirement_names(requirements: Path) -> List[str]:
    """从 requirements.txt 取出发行包名（去掉版本区间与环境标记）。"""
    names: List[str] = []
    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


# ----------------------------------------------------------------------
# 载体查找
# ----------------------------------------------------------------------

def classify_path(relative: str) -> str:
    """按路径判定载体性质（路径相对 site-packages）。

    - ``metadata`` 发行包自己的 METADATA/RECORD（不是许可证全文，只是声明）；
    - ``notice``   ``*.dist-info/licenses/`` 里的许可告知文件：随包分发但不被 import；
    - ``runtime``  被 import 的包目录里的文件：随产物分发；
    - ``docs`` / ``tests`` 只在文档或测试夹具里：不进产物。
    """
    lowered = "/" + relative.replace("\\", "/").lower()
    if lowered.endswith("/metadata") or lowered.endswith("/record"):
        return "metadata"
    if ".dist-info/" in lowered:
        # ``*.dist-info/`` 下的其余文件都是许可告知（LICENSE / COPYING 等），
        # 随包分发但不被 import。
        return "notice"
    if any(marker in lowered for marker in _TEST_MARKERS):
        return "tests"
    if any(marker in lowered for marker in _DOC_MARKERS):
        return "docs"
    return "runtime"


def find_carriers(package: str, root: Path, templates: Iterable[LicenseTemplate],
                  base: Optional[Path] = None) -> List[Carrier]:
    """在一个发行包的目录树里找许可证全文载体。

    ``base`` 是路径的基准（通常是 site-packages），载体的 ``path`` 相对它给出，
    这样 ``licenses/LICENSE`` 不会和有歧义的 ``LICENSE`` 混在一起。
    """
    base = base or root
    carriers: List[Carrier] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in _SKIP_SUFFIX:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for template in templates:
            if not template.matches(text):
                continue
            try:
                relative = path.relative_to(base).as_posix()
            except ValueError:
                relative = path.name
            carriers.append(Carrier(package=package, spdx_id=template.spdx_id,
                                    path=relative,
                                    kind=classify_path(relative),
                                    excerpt=_excerpt(text)))
    return carriers


def _excerpt(text: str, limit: int = 160) -> str:
    """取第一处命中的上下文，便于人工核对。"""
    for template in TEMPLATES:
        for pattern in template.patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                start = max(0, match.start() - 40)
                return " ".join(text[start:start + limit].split())
    return ""


def metadata_licenses(dist: metadata.Distribution) -> List[str]:
    """发行包元数据自己声明的许可（License-Expression + License 字段 + Classifier）。"""
    licenses: List[str] = []
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        licenses.append(expression)
    field = meta.get("License")
    if field and field not in licenses:
        licenses.append(field)
    for classifier in meta.get_all("Classifier", []):
        if "License ::" in classifier and classifier not in licenses:
            licenses.append(classifier.split("::")[-1].strip())
    return licenses


def inspect_distribution(name: str) -> Optional[DistributionFinding]:
    """核对单个发行包：元数据声明 + 包内许可证全文载体。"""
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    finding = DistributionFinding(name=dist.metadata["Name"] or name,
                                  version=dist.version or "",
                                  metadata_licenses=metadata_licenses(dist))
    site_root = Path(dist.locate_file(""))
    if not site_root.is_dir():
        return finding
    # 只扫这个发行包自己的顶层目录（``numpy/`` 与 ``numpy-2.5.3.dist-info/``），
    # 不能从 site-packages 根扫 —— 否则每个包都要重扫一遍全目录。
    tops: set = set()
    for item in dist.files or []:
        raw = Path(str(item))
        try:
            relative = raw.relative_to(site_root)
        except ValueError:
            if raw.is_absolute():
                continue
            relative = raw
        if relative.parts:
            first = relative.parts[0]
            # ``../../../bin/f2py`` 这类条目指向 site-packages 之外（scripts 目录），
            # 扫描它们会把整棵相邻目录树卷进来，必须排除。
            if first in {".", ".."} or first.startswith(".."):
                continue
            tops.add(first)
    for top in sorted(tops):
        target = site_root / top
        if not target.is_dir():
            continue
        finding.carriers.extend(
            find_carriers(finding.name, target, TEMPLATES, base=site_root))
    return finding


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------

def collect(requirements: Path) -> List[DistributionFinding]:
    """核对 requirements.txt 里的全部已安装依赖。"""
    findings: List[DistributionFinding] = []
    for name in requirement_names(requirements):
        finding = inspect_distribution(name)
        if finding is not None:
            findings.append(finding)
    return findings


def verdict_for(finding: DistributionFinding, spdx_id: str) -> str:
    """某个 SPDX ID 在这个发行包里的判定。"""
    canonical = canonical_spdx(spdx_id)
    hits = [item for item in finding.carriers if item.spdx_id == canonical]
    if hits:
        kinds = {item.kind for item in hits}
        for kind in ("runtime", "notice", "docs", "tests", "metadata"):
            if kind in kinds:
                return kind
    if any(canonical.lower() in expression.lower()
           for expression in finding.metadata_licenses):
        return "metadata"
    return "none"


def render_report(findings: List[DistributionFinding],
                  expectations: List[Tuple[str, str]]) -> str:
    """人类可读报告。"""
    lines: List[str] = ["FOSSA 许可证 ID 载体核对", "=" * 60]
    if expectations:
        lines.append("")
        lines.append("待核对条目（--expect）：")
        for package, spdx_id in expectations:
            finding = next((item for item in findings
                            if item.name.lower() == package.lower()), None)
            if finding is None:
                lines.append(f"  [未安装]  {package}:{spdx_id} —— 本机没有该发行包，"
                             f"无法核对（不代表扫描器有误）")
                continue
            verdict = verdict_for(finding, spdx_id)
            note = {"runtime": "载体在运行时代码里：随产物分发，需人工确认",
                    "notice": "载体是随包分发的许可告知文件：不是被 import 的代码，"
                              "但要随产物提供",
                    "docs": "载体只在文档里：不进产物、不进 import",
                    "tests": "载体只在测试夹具里：不进产物",
                    "metadata": "发行包元数据自己声明了该许可",
                    "none": "全库无该 ID 的许可证全文载体：扫描器误判"
                    }.get(verdict, verdict)
            lines.append(f"  [{verdict:8s}] {package}:{spdx_id} —— {note}")
            canonical = canonical_spdx(spdx_id)
            for carrier in finding.carriers:
                if carrier.spdx_id == canonical:
                    lines.append(f"              {carrier.path}（{carrier.kind}）")
    lines.append("")
    lines.append("各依赖的载体明细：")
    for finding in findings:
        if not finding.carriers and not finding.metadata_licenses:
            continue
        lines.append(f"  {finding.name} {finding.version}")
        if finding.metadata_licenses:
            lines.append(f"    元数据: {'; '.join(finding.metadata_licenses)}")
        for carrier in finding.carriers:
            lines.append(f"    [{carrier.kind:7s}] {carrier.spdx_id} ← {carrier.path}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requirements", type=Path,
                        default=ROOT / "requirements.txt")
    parser.add_argument("--expect", action="append", default=[],
                        help="待核对的 SPDX ID，形如 numpy:GPL-1.0（可重复）")
    parser.add_argument("--fail-on", choices=("none", "runtime", "notice", "any"),
                        default="none",
                        help="出现哪类判定时以非零退出（默认 none：只报告不拦截）。"
                             "CI 里对「已判定为误判的 ID」用 runtime：一旦它出现"
                             "运行时载体，说明依赖结构变了，必须重新复核")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    findings = collect(args.requirements)
    expectations = [tuple(item.split(":", 1)) for item in args.expect
                    if ":" in item]

    if args.json:
        payload = {"distributions": [item.as_dict() for item in findings],
                   "expectations": [
                       {"package": package, "spdx_id": spdx_id,
                        "verdict": verdict_for(
                            next((item for item in findings
                                  if item.name.lower() == package.lower()),
                                 DistributionFinding(name=package)), spdx_id)}
                       for package, spdx_id in expectations]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_report(findings, expectations))

    # 门禁语义（--fail-on）：
    #   none     只报告，不拦截（默认）
    #   runtime  出现**运行时载体**即失败 —— 用于「已判定为误判的 ID」：它一旦
    #             变成真实载体，说明依赖结构变了，原结论作废，必须重新复核
    #   notice   出现随包分发的许可告知文件即失败
    #   any      任何非「无载体」判定都失败
    misjudged: List[str] = []
    blocking: List[str] = []
    for package, spdx_id in expectations:
        finding = next((item for item in findings
                        if item.name.lower() == package.lower()), None)
        if finding is None:
            continue
        verdict = verdict_for(finding, spdx_id)
        if verdict == "none":
            misjudged.append(f"{package}:{spdx_id}")
        if (args.fail_on == "runtime" and verdict == "runtime") or \
                (args.fail_on == "notice" and verdict in {"runtime", "notice"}) or \
                (args.fail_on == "any" and verdict != "none"):
            blocking.append(f"{package}:{spdx_id}（{verdict}）")
    if misjudged:
        print("\n以下 ID 在依赖里找不到载体，可按「扫描器误判」处理："
              + ", ".join(misjudged), file=sys.stderr)
    if blocking:
        print("\n以下 ID 出现了新的载体，必须重新复核后再在后台放行："
              + ", ".join(blocking), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
