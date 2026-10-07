#!/usr/bin/env python3
"""
Architecture Lint — Live2D Master Agent (FF-1 红线)
====================================================

落地 docs/architecture/index.md §4 FF-1「依赖方向正确性（三栈解耦）」：
    ① Python 不 import Go/TS 包
    ② Go 不 import Python 源码（仅通过 subprocess + FS）
    ③ web/ 不直接读磁盘（除服务端合理场景）
    ④ 依赖方向不允许反向（core < live2d_builder < drivers/llm_bridge）

四条规则一旦违规，脚本 exit 1，对应 FF-1「PR 阻断」失败响应。

用法
----
    python scripts/lint_architecture.py            # 扫整个项目，违规时 exit 1
    python scripts/lint_architecture.py --quiet    # 只输出违规摘要
    python scripts/lint_architecture.py --json     # 输出 JSON 给 CI 消费
    python scripts/lint_architecture.py --help

已知违规清单（截至 v0.10.0，待 Sprint 2 修复，**脚本会如实报出**）
---------------------------------------------------------------
规则 4 在当前代码库发现 8 处真实反向依赖，均不在用户的「误报规避」清单内。
脚本**不会**为了让 CI 变绿而隐藏这些违规——诚实标注如下，待 Sprint 2 通过
端口/适配器或依赖注入解耦后清除：

    core/ 作为最底层内核，违规引用上层（5 处，分布在 2 个文件）：
      1. core/cli.py:146      → from drivers.desktop_pet.runner import PetRunner
      2. core/cli.py:162      → from llm_bridge.chat_session import ChatSession
      3. core/cli.py:163      → from llm_bridge.providers.router import LLMRouter
      4. core/cli.py:164      → from llm_bridge.emotion.analyzer import EmotionAnalyzer
      5. core/workflow.py:38  → from drivers.desktop_pet.animator import DesktopPetAnimator
      6. core/workflow.py:493 → from live2d_builder.pipeline import RiggingPipeline

    live2d_builder/ 违规引用 drivers/（2 处）：
      7. live2d_builder/exporter/__init__.py:8
           → from drivers.live2d_runtime.moc3_verify import ...
      8. live2d_builder/exporter/moc3_pipeline.py:39
           → from drivers.live2d_runtime.moc3_verify import ...

    建议：在 core 定义 Protocol 抽象接口（PetAnimatorProtocol / BuilderProtocol /
    ChatGatewayProtocol），上游层实现并注入；moc3_verify 的纯检查逻辑下沉到
    live2d_builder/validator 或独立 sdk 包。

误报规避（第三方包，规则设计天然不报）
------------------------------------
    - core/segment_engine/semantic.py 的 `from segment_anything import ...`
      → 第三方包，顶层模块名 `segment_anything` 不在项目层白名单内。
    - drivers/live2d_runtime/*.py 的 `import live2d.v3 as sdk`
      → 第三方包 live2d-py，顶层模块名 `live2d` ≠ 项目层 `live2d_builder`（精确前缀匹配）。
    - drivers/live2d_runtime/native.py 的 `importlib.import_module('live2d.v3')`
      → 同上，且为动态加载，静态 lint 不覆盖。
    - live2d_builder/exporter/moc3_pipeline.py 引用 core.* 是正向依赖，规则 4 不报。

设计取舍
--------
    - 规则 1 用 AST + 字面量正则双保险（动态 import 如 importlib.import_module
      无法被 AST 当作 Import 节点，需正则兜底）。
    - 规则 2 用正则解析 Go import 块（不依赖 Go 工具链），允许标准库 /
      github.com 第三方 / live2d-api 项目内部包。
    - 规则 3 用正则扫 fs API，对 Next.js 服务端场景（pages/api、getServerSideProps、
      next.config、sw.js 等）做白名单豁免。
    - 规则 4 用 AST 解析 Python import，按顶层包名归属项目层；相对 import
      （node.level > 0）默认不跨层，跳过反向检查。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence

__all__ = [
    "Violation",
    "RuleResult",
    "check_rule1_python_no_go_ts",
    "check_rule2_go_no_python",
    "check_rule3_web_no_fs",
    "check_rule4_no_reverse_dep",
    "run_all",
    "format_text",
    "format_json",
    "main",
]

PROJECT_ROOT_DEFAULT = Path(__file__).resolve().parent.parent

PYTHON_GO_TS_EXT_RE = re.compile(r"\.(?:go|ts|tsx)(?:\b|$)")
DYNAMIC_IMPORT_RE = re.compile(
    r"""(?:__import__|importlib\.import_module)\s*\(\s*["']([^"']+)["']"""
)

GO_IMPORT_BLOCK_RE = re.compile(r"import\s*\(([^)]*)\)", re.DOTALL)
GO_SINGLE_IMPORT_RE = re.compile(r'^import\s+"([^"]+)"', re.MULTILINE)
GO_QUOTED_PATH_RE = re.compile(r'"([^"]+)"')

WEB_FS_API_RE = re.compile(r"\bfs\.(readFileSync|readFile|writeFileSync|writeFile|promises)\b")
WEB_FS_IMPORT_RE = re.compile(r"""(?:from\s+['"]fs['"]|require\(\s*['"]fs['"]\s*\))""")
WEB_IGNORE_DIRS = frozenset(
    {
        "node_modules",
        ".next",
        "out",
        "dist",
        "build",
        ".cache",
        "coverage",
    }
)
WEB_SERVER_PATH_MARKERS = (
    "/server/",
    "/server-side/",
    "/api/",
    "getServerSideProps",
    "getStaticProps",
    "next.config",
    "sw.js",
    "_document",
    "_app",
)

PROJECT_LAYERS = ("core", "live2d_builder", "drivers", "llm_bridge")
FORBIDDEN_DOWNSTREAM_FROM = {
    "core": ("drivers", "live2d_builder", "llm_bridge"),
    "live2d_builder": ("drivers", "llm_bridge"),
    "llm_bridge": ("drivers",),
}
PY_SOURCE_DIRS_FOR_RULE1 = ("core", "live2d_builder", "drivers", "llm_bridge")
PY_SOURCE_FILES_FOR_RULE1 = ("api_server.py",)


@dataclass(frozen=True)
class Violation:
    rule_id: str
    rule_name: str
    file: str
    line: int
    col: int
    detail: str
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }


@dataclass
class RuleResult:
    rule_id: str
    rule_name: str
    files_scanned: int = 0
    violations: List[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "passed": self.passed,
            "files_scanned": self.files_scanned,
            "violation_count": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
        }


def _line_col(text: str, pos: int) -> tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    last_nl = text.rfind("\n", 0, pos)
    col = pos - last_nl
    return line, col


def _iter_py_files(targets: Iterable[Path]) -> Iterator[Path]:
    for t in targets:
        if not t.exists():
            continue
        if t.is_file() and t.suffix == ".py":
            yield t
        elif t.is_dir():
            yield from sorted(t.rglob("*.py"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _module_top_layer(module: str) -> Optional[str]:
    if not module:
        return None
    top = module.lstrip(".").split(".")[0]
    if top in PROJECT_LAYERS:
        return top
    return None


def check_rule1_python_no_go_ts(project_root: Path) -> RuleResult:
    """规则 1：Python 源码不允许 import 引用 .go / .ts / .tsx 源文件。"""
    result = RuleResult(rule_id="R1", rule_name="Python 不引用 Go/TS 源码")
    targets: List[Path] = [project_root / d for d in PY_SOURCE_DIRS_FOR_RULE1]
    targets += [project_root / f for f in PY_SOURCE_FILES_FOR_RULE1]

    for py_file in _iter_py_files(targets):
        result.files_scanned += 1
        rel = py_file.relative_to(project_root).as_posix()
        text = _read_text(py_file)

        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None

        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if PYTHON_GO_TS_EXT_RE.search(alias.name):
                            result.violations.append(
                                Violation(
                                    "R1",
                                    result.rule_name,
                                    rel,
                                    node.lineno,
                                    node.col_offset + 1,
                                    f'import "{alias.name}"（跨栈引用）',
                                    "Python 不应直接 import Go/TS 源码；三栈之间走 subprocess + JSON / HTTP",
                                )
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if PYTHON_GO_TS_EXT_RE.search(mod):
                        result.violations.append(
                            Violation(
                                "R1",
                                result.rule_name,
                                rel,
                                node.lineno,
                                node.col_offset + 1,
                                f'from "{mod}" import ...（跨栈引用）',
                                "Python 不应直接 import Go/TS 源码；三栈之间走 subprocess + JSON / HTTP",
                            )
                        )

        for m in DYNAMIC_IMPORT_RE.finditer(text):
            target = m.group(1)
            if PYTHON_GO_TS_EXT_RE.search(target):
                line, col = _line_col(text, m.start())
                result.violations.append(
                    Violation(
                        "R1",
                        result.rule_name,
                        rel,
                        line,
                        col,
                        f'动态 import "{target}"（跨栈引用）',
                        "Python 不应通过 __import__/importlib 加载 Go/TS 源码",
                    )
                )

    return result


def check_rule2_go_no_python(project_root: Path) -> RuleResult:
    """规则 2：api/ 下 .go 文件不允许 import Python 源码目录。"""
    result = RuleResult(rule_id="R2", rule_name="Go 不引用 Python 源码")
    api_dir = project_root / "api"
    if not api_dir.is_dir():
        return result

    python_src_segments = ("core", "drivers", "live2d_builder", "llm_bridge")

    def _is_python_source_import(imp: str) -> bool:
        if imp == "python":
            return True
        parts = imp.split("/")
        for seg in python_src_segments:
            if seg in parts:
                return True
        return False

    for go_file in sorted(api_dir.rglob("*.go")):
        if go_file.name.endswith("_test.go"):
            continue
        result.files_scanned += 1
        rel = go_file.relative_to(project_root).as_posix()
        text = _read_text(go_file)

        collected: List[str] = []
        for block in GO_IMPORT_BLOCK_RE.findall(text):
            collected.extend(GO_QUOTED_PATH_RE.findall(block))
        collected.extend(GO_SINGLE_IMPORT_RE.findall(text))

        seen_positions: dict[str, int] = {}
        for imp in collected:
            if not _is_python_source_import(imp):
                continue
            quote = f'"{imp}"'
            start_from = seen_positions.get(imp, 0)
            pos = text.find(quote, start_from)
            if pos == -1:
                pos = text.find(imp, start_from)
            if pos == -1:
                line = col = 0
            else:
                line, col = _line_col(text, pos)
                seen_positions[imp] = pos + 1
            result.violations.append(
                Violation(
                    "R2",
                    result.rule_name,
                    rel,
                    line,
                    col,
                    f'import "{imp}"（引用 Python 源码）',
                    "Go 通过 subprocess + JSON 调用 Python，不应直接 import Python 源码",
                )
            )

    return result


def check_rule3_web_no_fs(project_root: Path) -> RuleResult:
    """规则 3：web/ 下 .ts/.tsx 不允许客户端组件直接使用 fs API。"""
    result = RuleResult(rule_id="R3", rule_name="前端不直接读磁盘")
    web_dir = project_root / "web"
    if not web_dir.is_dir():
        return result

    for ext in ("*.ts", "*.tsx"):
        for ts_file in sorted(web_dir.rglob(ext)):
            rel = ts_file.relative_to(project_root).as_posix()
            if any(part in WEB_IGNORE_DIRS for part in ts_file.parts):
                continue
            if any(marker in rel for marker in WEB_SERVER_PATH_MARKERS):
                continue
            result.files_scanned += 1
            text = _read_text(ts_file)

            for m in WEB_FS_API_RE.finditer(text):
                line, col = _line_col(text, m.start())
                result.violations.append(
                    Violation(
                        "R3",
                        result.rule_name,
                        rel,
                        line,
                        col,
                        f"使用 fs.{m.group(1)}",
                        "Next.js 客户端组件不应直接读写磁盘；改用 HTTP/WS 调 Go API",
                    )
                )

            for m in WEB_FS_IMPORT_RE.finditer(text):
                line, col = _line_col(text, m.start())
                result.violations.append(
                    Violation(
                        "R3",
                        result.rule_name,
                        rel,
                        line,
                        col,
                        "import fs / require('fs')",
                        "前端组件不应 import 'fs'；服务端逻辑请放到 pages/api 或显式 server 文件",
                    )
                )

    return result


def check_rule4_no_reverse_dep(project_root: Path) -> RuleResult:
    """规则 4：项目层依赖方向不允许反向（core < live2d_builder < drivers/llm_bridge）。"""
    result = RuleResult(rule_id="R4", rule_name="依赖方向不反向")

    for owner in PROJECT_LAYERS:
        owner_dir = project_root / owner
        if not owner_dir.is_dir():
            continue
        forbidden = FORBIDDEN_DOWNSTREAM_FROM.get(owner, ())
        if not forbidden:
            continue

        for py_file in sorted(owner_dir.rglob("*.py")):
            result.files_scanned += 1
            rel = py_file.relative_to(project_root).as_posix()
            text = _read_text(py_file)
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level and node.level > 0:
                        continue
                    modules = [node.module or ""]
                else:
                    continue

                for mod in modules:
                    target_layer = _module_top_layer(mod)
                    if target_layer is None:
                        continue
                    if target_layer in forbidden:
                        result.violations.append(
                            Violation(
                                "R4",
                                result.rule_name,
                                rel,
                                node.lineno,
                                node.col_offset + 1,
                                f"{owner} imports {target_layer}（反向依赖：{mod}）",
                                f"{owner}/ 不应 import {target_layer}/；请通过端口/适配器或依赖注入解耦（待 Sprint 2）",
                            )
                        )

    return result


def run_all(project_root: Path) -> List[RuleResult]:
    return [
        check_rule1_python_no_go_ts(project_root),
        check_rule2_go_no_python(project_root),
        check_rule3_web_no_fs(project_root),
        check_rule4_no_reverse_dep(project_root),
    ]


def format_text(results: Sequence[RuleResult], quiet: bool = False) -> str:
    lines: List[str] = [
        "Architecture Lint — Live2D Master Agent",
        "========================================",
        "",
    ]
    if quiet:
        for r in results:
            status = "PASS" if r.passed else f"FAIL ({len(r.violations)})"
            lines.append(f"[{r.rule_id}] {r.rule_name}: {status} ({r.files_scanned} files)")
        lines.append("")
    else:
        for r in results:
            mark = "✓ PASS" if r.passed else "✗ FAIL"
            lines.append(f"[Rule {r.rule_id[-1]}] {r.rule_name}")
            if r.passed:
                lines.append(f"  {mark} (扫描了 {r.files_scanned} 个文件)")
            else:
                lines.append(f"  {mark}（{len(r.violations)} 处违规）")
                for v in r.violations:
                    lines.append(
                        f"    {v.file}:{v.line}:{v.col} — {v.detail}"
                    )
                    if v.suggestion:
                        lines.append(f"    建议：{v.suggestion}")
            lines.append("")

    total_violations = sum(len(r.violations) for r in results)
    failed = sum(1 for r in results if not r.passed)
    if total_violations == 0:
        lines.append(f"Summary: 全部 {len(results)} 条规则通过，零违规")
    else:
        lines.append(
            f"Summary: {failed}/{len(results)} 规则未通过，发现 {total_violations} 处违规"
        )
    return "\n".join(lines) + "\n"


def format_json(results: Sequence[RuleResult]) -> str:
    total_violations = sum(len(r.violations) for r in results)
    payload = {
        "tool": "lint_architecture",
        "version": "1.0",
        "ff": "FF-1",
        "passed": total_violations == 0,
        "total_violations": total_violations,
        "rules": [r.to_dict() for r in results],
        "violations": [v.to_dict() for r in results for v in r.violations],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_architecture.py",
        description="FF-1 架构依赖方向 lint（Python+Go+Next 三栈解耦红线）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 = 全部规则通过；1 = 存在违规（对应 FF-1 PR 阻断）。\n"
            "详见 docs/architecture/index.md §4 FF-1。"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="只输出违规摘要")
    parser.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON 给 CI 消费")
    parser.add_argument(
        "--root",
        default=None,
        help="项目根目录（默认：脚本所在目录的上一级）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.root:
        project_root = Path(args.root).resolve()
    else:
        project_root = PROJECT_ROOT_DEFAULT

    if not project_root.exists():
        print(f"错误：项目根目录不存在：{project_root}", file=sys.stderr)
        return 2

    results = run_all(project_root)

    if args.as_json:
        print(format_json(results))
    else:
        print(format_text(results, quiet=args.quiet), end="")

    total_violations = sum(len(r.violations) for r in results)
    return 1 if total_violations > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
