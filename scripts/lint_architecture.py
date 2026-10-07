#!/usr/bin/env python3
"""
Architecture Lint — Live2D Master Agent (FF-1 红线)
====================================================

落地 docs/architecture/index.md §4 FF-1「依赖方向正确性（三栈解耦）」：
    ① Python 不 import Go/TS 包
    ② Go 不 import Python 源码（仅通过 subprocess + FS）
    ③ web/ 不直接读磁盘（除服务端合理场景）
    ④ 依赖方向不允许反向（core < live2d_builder < drivers/llm_bridge；
       且 live2d_builder 不依赖服务层 api_server）

四条规则一旦违规，脚本 exit 1，对应 FF-1「PR 阻断」失败响应。

用法
----
    python scripts/lint_architecture.py              # 扫整个项目，违规时 exit 1
    python scripts/lint_architecture.py --quiet      # 只输出违规摘要
    python scripts/lint_architecture.py --json       # 输出 JSON 给 CI 消费
    python scripts/lint_architecture.py --root core  # 只扫 core/ 子目录（scope 模式）
    python scripts/lint_architecture.py --help

--root 语义
-----------
    不传 / 传项目根本身          → 全量扫描（默认）
    传项目根的直接子目录之一     → scope 模式，只扫该子目录与相关规则
                                  合法值：core / live2d_builder / drivers /
                                          llm_bridge / api / web
    传任意其它已存在的目录       → 把它当项目根（脚本据此解析相对路径）

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
SERVICE_LAYER_MODULES = ("api_server",)
_LAYER_TOKENS = frozenset(PROJECT_LAYERS + SERVICE_LAYER_MODULES)
FORBIDDEN_DOWNSTREAM_FROM = {
    "core": ("drivers", "live2d_builder", "llm_bridge"),
    "live2d_builder": ("drivers", "llm_bridge", "api_server"),
    "llm_bridge": ("drivers",),
}
PY_SOURCE_DIRS_FOR_RULE1 = ("core", "live2d_builder", "drivers", "llm_bridge")
PY_SOURCE_FILES_FOR_RULE1 = ("api_server.py",)

# FF-1 跳过规则：以下目录 / 文件不参与依赖方向检查
#   - 缓存 / 虚拟环境 / 构建产物 / 依赖目录：不是项目源码
#   - tools/archive：归档不参与（脚本会跳过 tools/<archive> 子树）
#   - 测试文件（test_*.py / *_test.py / *_test.go）：测试允许反向依赖做集成验证
SKIP_DIR_NAMES = frozenset({
    "__pycache__", "__pypackages__",
    ".venv", ".venv38", ".venv39", ".venv310", ".venv311", ".venv312", ".venv313",
    "node_modules", ".next", ".nuxt",
    ".pytest_cache", ".mypy_cache", ".tox", ".cache", "htmlcov",
    "dist", "build", "out", "coverage",
})
# --root 可指定的子目录（scope 模式）
SCOPEABLE_DIRS = ("core", "live2d_builder", "drivers", "llm_bridge", "api", "web")


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


def _is_test_file_name(name: str) -> bool:
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
    )


def _should_skip_parts(parts: tuple) -> bool:
    """根据路径片段判断是否跳过（缓存目录 / 归档子树 / 虚拟环境等）。"""
    if any(p in SKIP_DIR_NAMES for p in parts):
        return True
    for i, p in enumerate(parts):
        if p == "tools" and i + 1 < len(parts) and parts[i + 1] == "archive":
            return True
    return False


def _scoped_target(target: Path, scope: Optional[Path]) -> Optional[Path]:
    """根据 scope 裁剪扫描目标。

    - scope=None：返回 target（若存在）
    - target 在 scope 内（target==scope 或 scope 是 target 上级）→ 返回 target
    - target 包含 scope（scope 是 target 的后代）→ 返回 scope（裁剪）
    - 无交集 → None（跳过该目标）
    """
    if scope is None:
        return target if target.exists() else None
    if not target.exists():
        return None
    ta = target.resolve()
    sc = scope.resolve()
    if ta == sc or sc in ta.parents:
        return ta
    if ta in sc.parents:
        return sc
    return None


def _iter_py_files(targets: Iterable[Path]) -> Iterator[Path]:
    for t in targets:
        if not t.exists():
            continue
        if t.is_file() and t.suffix == ".py":
            if _should_skip_parts(t.parts) or _is_test_file_name(t.name):
                continue
            yield t
        elif t.is_dir():
            for p in sorted(t.rglob("*.py")):
                if _should_skip_parts(p.parts) or _is_test_file_name(p.name):
                    continue
                yield p


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _module_top_layer(module: str) -> Optional[str]:
    if not module:
        return None
    top = module.lstrip(".").split(".")[0]
    if top in _LAYER_TOKENS:
        return top
    return None


def check_rule1_python_no_go_ts(project_root: Path, scope: Optional[Path] = None) -> RuleResult:
    """规则 1：Python 源码不允许 import 引用 .go / .ts / .tsx 源文件。"""
    result = RuleResult(rule_id="R1", rule_name="Python 不引用 Go/TS 源码")
    raw_targets: List[Path] = [project_root / d for d in PY_SOURCE_DIRS_FOR_RULE1]
    raw_targets += [project_root / f for f in PY_SOURCE_FILES_FOR_RULE1]
    targets: List[Path] = []
    for t in raw_targets:
        st = _scoped_target(t, scope)
        if st is not None:
            targets.append(st)

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


def check_rule2_go_no_python(project_root: Path, scope: Optional[Path] = None) -> RuleResult:
    """规则 2：api/ 下 .go 文件不允许 import Python 源码目录。"""
    result = RuleResult(rule_id="R2", rule_name="Go 不引用 Python 源码")
    api_dir = project_root / "api"
    scan_root = _scoped_target(api_dir, scope)
    if scan_root is None or not scan_root.is_dir():
        return result

    python_src_segments = ("core", "drivers", "live2d_builder", "llm_bridge")

    def _is_python_source_import(imp: str) -> bool:
        parts = imp.split("/")
        if "python" in parts:
            return True
        for seg in python_src_segments:
            if seg in parts:
                return True
        return False

    for go_file in sorted(scan_root.rglob("*.go")):
        if _is_test_file_name(go_file.name) or _should_skip_parts(go_file.parts):
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


def check_rule3_web_no_fs(project_root: Path, scope: Optional[Path] = None) -> RuleResult:
    """规则 3：web/ 下 .ts/.tsx 不允许客户端组件直接使用 fs API。"""
    result = RuleResult(rule_id="R3", rule_name="前端不直接读磁盘")
    web_dir = project_root / "web"
    scan_root = _scoped_target(web_dir, scope)
    if scan_root is None or not scan_root.is_dir():
        return result

    for ext in ("*.ts", "*.tsx"):
        for ts_file in sorted(scan_root.rglob(ext)):
            rel = ts_file.relative_to(project_root).as_posix()
            if _should_skip_parts(ts_file.parts):
                continue
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


def check_rule4_no_reverse_dep(project_root: Path, scope: Optional[Path] = None) -> RuleResult:
    """规则 4：项目层依赖方向不允许反向（core < live2d_builder < drivers/llm_bridge）。"""
    result = RuleResult(rule_id="R4", rule_name="依赖方向不反向")

    for owner in PROJECT_LAYERS:
        owner_dir = project_root / owner
        scan_root = _scoped_target(owner_dir, scope)
        if scan_root is None or not scan_root.is_dir():
            continue
        forbidden = FORBIDDEN_DOWNSTREAM_FROM.get(owner, ())
        if not forbidden:
            continue

        for py_file in sorted(scan_root.rglob("*.py")):
            if _should_skip_parts(py_file.parts) or _is_test_file_name(py_file.name):
                continue
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


def run_all(project_root: Path, scope: Optional[Path] = None) -> List[RuleResult]:
    return [
        check_rule1_python_no_go_ts(project_root, scope),
        check_rule2_go_no_python(project_root, scope),
        check_rule3_web_no_fs(project_root, scope),
        check_rule4_no_reverse_dep(project_root, scope),
    ]


def _scope_rel(scope: Optional[Path], project_root: Optional[Path]) -> Optional[str]:
    if scope is None or project_root is None:
        return None
    try:
        return scope.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return scope.as_posix()


def format_text(
    results: Sequence[RuleResult],
    quiet: bool = False,
    scope: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> str:
    lines: List[str] = [
        "━━━ FF-1 依赖方向 lint ━━━",
        "",
    ]
    sr = _scope_rel(scope, project_root)
    if sr is not None:
        lines.append(f"扫描范围（--root 限定）: {sr}/")
    else:
        lines.append(
            "扫描范围: core/ live2d_builder/ drivers/ llm_bridge/ api_server.py api/ web/"
        )
    lines.append("")

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

    total_files = sum(r.files_scanned for r in results)
    total_violations = sum(len(r.violations) for r in results)
    lines.append("━━━ 结果 ━━━")
    lines.append(f"扫描文件: {total_files}")
    lines.append(f"违规:     {total_violations}")
    if total_violations == 0:
        lines.append("结论:     全部规则通过，零违规")
    else:
        failed = sum(1 for r in results if not r.passed)
        lines.append(f"结论:     {failed}/{len(results)} 规则未通过")
    return "\n".join(lines) + "\n"


def format_json(
    results: Sequence[RuleResult],
    scope: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> str:
    total_violations = sum(len(r.violations) for r in results)
    total_files = sum(r.files_scanned for r in results)
    payload = {
        "tool": "lint_architecture",
        "version": "1.1",
        "ff": "FF-1",
        "passed": total_violations == 0,
        "total_violations": total_violations,
        "total_files_scanned": total_files,
        "scope": _scope_rel(scope, project_root),
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
        help=(
            "项目根目录（默认：脚本上一级）。"
            "若指向项目根的直接子目录（core/live2d_builder/drivers/llm_bridge/api/web），"
            "则进入 scope 模式，只扫该子目录与相关规则。"
        ),
    )
    return parser


def _resolve_project_root_and_scope(raw_root: Optional[str]) -> tuple[Path, Optional[Path]]:
    """把 --root 解析成 (project_root, scope)。

    - raw_root 为空 → (项目根, None)
    - 指向项目根本身 → (项目根, None)
    - 指向项目根的直接子目录（SCOPEABLE_DIRS）→ (项目根, 该子目录)
    - 指向其它已存在的目录 → (该目录, None)
    - 不存在 → 抛 FileNotFoundError
    """
    project_root = PROJECT_ROOT_DEFAULT
    if not raw_root:
        return project_root, None
    candidate = Path(raw_root).resolve()
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    if candidate == project_root:
        return project_root, None
    if candidate.parent == project_root and candidate.name in SCOPEABLE_DIRS:
        return project_root, candidate
    return candidate, None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        project_root, scope = _resolve_project_root_and_scope(args.root)
    except FileNotFoundError as e:
        print(f"错误：路径不存在：{e.filename}", file=sys.stderr)
        return 2

    results = run_all(project_root, scope=scope)

    if args.as_json:
        print(format_json(results, scope=scope, project_root=project_root))
    else:
        print(
            format_text(results, quiet=args.quiet, scope=scope, project_root=project_root),
            end="",
        )

    total_violations = sum(len(r.violations) for r in results)
    return 1 if total_violations > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
