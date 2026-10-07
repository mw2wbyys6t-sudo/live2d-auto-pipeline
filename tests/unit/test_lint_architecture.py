"""FF-1 架构依赖方向 lint 的单元测试。

覆盖 scripts/lint_architecture.py 的四条规则 + 跳过逻辑（FF-1 红线）。
用 tmp_path 构造迷你项目结构，直接调用内部 check_* 函数（不走 subprocess），
对应 docs/architecture/index.md §4 FF-1 的可测自动化要求。
"""
import sys
from pathlib import Path

# 把 scripts/ 加入 sys.path 以便 import lint_architecture
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import lint_architecture as la  # noqa: E402


def _write(rel_project_path: Path, text: str) -> Path:
    """在 tmp_path 项目下写文件（自动建父目录）。"""
    rel_project_path.parent.mkdir(parents=True, exist_ok=True)
    rel_project_path.write_text(text, encoding="utf-8")
    return rel_project_path


def _violations_of(result: la.RuleResult, rule_id: str) -> list:
    return [v for v in result.violations if v.rule_id == rule_id]


# ============================================================
# 任务用例 1：合规 Python 文件全放行
# ============================================================

def test_clean_python_file(tmp_path):
    """合规 Python：只 import 标准库 + 同层模块，四条规则全放行。"""
    _write(
        tmp_path / "core" / "workflow.py",
        "import os\nimport sys\nfrom core.utils import helper\nVALUE = 1\n",
    )
    results = la.run_all(tmp_path)
    assert all(r.passed for r in results), [
        (r.rule_id, [v.detail for v in r.violations]) for r in results
    ]
    assert sum(len(r.violations) for r in results) == 0


# ============================================================
# 任务用例 2：Python 引用 .go 路径必须被抓（规则 1）
# ============================================================

def test_python_imports_go_path(tmp_path):
    """规则 1：Python from-import 引用 .go 路径必须被抓。"""
    _write(tmp_path / "core" / "bad.py", "from api.main.go import handler\n")
    result = la.check_rule1_python_no_go_ts(tmp_path)
    viol = _violations_of(result, "R1")
    assert len(viol) == 1
    assert ".go" in viol[0].detail


def test_python_dynamic_import_go_path(tmp_path):
    """规则 1 双保险：__import__('...go') 动态引用也要抓（正则兜底 AST 之外的动态加载）。"""
    _write(
        tmp_path / "drivers" / "runtime.py",
        "mod = __import__('api.main.go')\n",
    )
    result = la.check_rule1_python_no_go_ts(tmp_path)
    viol = _violations_of(result, "R1")
    assert len(viol) == 1


# ============================================================
# 任务用例 3：Go import Python 源码路径必须被抓（规则 2）
# ============================================================

def test_go_imports_python_path(tmp_path):
    """规则 2：Go 文件 import 含 python 字样的源码路径必须被抓。"""
    _write(
        tmp_path / "api" / "main.go",
        'package main\nimport "python/something"\n',
    )
    result = la.check_rule2_go_no_python(tmp_path)
    viol = _violations_of(result, "R2")
    assert len(viol) == 1


def test_go_imports_project_python_pkg(tmp_path):
    """规则 2：Go 文件 import 项目 Python 源码包（core/drivers/...）也要抓。"""
    _write(
        tmp_path / "api" / "svc.go",
        'package main\nimport "live2d-api/thirdparty/core/engine"\n',
    )
    result = la.check_rule2_go_no_python(tmp_path)
    viol = _violations_of(result, "R2")
    assert len(viol) == 1


def test_go_python_bridge_is_allowed(tmp_path):
    """规则 2 误报规避：Go 自己的 python_bridge 包名不算违规。"""
    _write(
        tmp_path / "api" / "bridge.go",
        'package main\nimport "live2d-api/services/python_bridge"\n',
    )
    result = la.check_rule2_go_no_python(tmp_path)
    assert result.violations == []


# ============================================================
# 任务用例 4：前端直接读磁盘业务路径必须被抓（规则 3）
# ============================================================

def test_web_reads_disk_business_path(tmp_path):
    """规则 3：web 下 TS 文件 fs.readFileSync 读业务路径必须被抓。"""
    _write(
        tmp_path / "web" / "lib" / "export.ts",
        "import fs from 'fs'\n"
        "const data = fs.readFileSync('output/data.json')\n"
        "export default data\n",
    )
    result = la.check_rule3_web_no_fs(tmp_path)
    viol = _violations_of(result, "R3")
    assert len(viol) >= 1


def test_web_server_route_is_exempt(tmp_path):
    """规则 3 豁免：pages/api 下的服务端路由允许使用 fs。"""
    _write(
        tmp_path / "web" / "pages" / "api" / "export.ts",
        "import fs from 'fs'\n"
        "const data = fs.readFileSync('output/data.json')\n"
        "export default data\n",
    )
    result = la.check_rule3_web_no_fs(tmp_path)
    assert result.violations == []


# ============================================================
# 任务用例 5：core 反向依赖 drivers 必须被抓（规则 4）
# ============================================================

def test_reverse_dependency_core_to_drivers(tmp_path):
    """规则 4：core 反向 import drivers 必须被抓。"""
    _write(tmp_path / "core" / "foo.py", "import drivers.bar\n")
    result = la.check_rule4_no_reverse_dep(tmp_path)
    viol = _violations_of(result, "R4")
    assert len(viol) == 1
    assert "core" in viol[0].detail
    assert "drivers" in viol[0].detail


def test_reverse_dependency_live2d_builder_to_api_server(tmp_path):
    """规则 4c：live2d_builder 反向 import 服务层 api_server 必须被抓。"""
    _write(tmp_path / "live2d_builder" / "pipeline.py", "import api_server\n")
    result = la.check_rule4_no_reverse_dep(tmp_path)
    viol = _violations_of(result, "R4")
    assert len(viol) == 1
    assert "api_server" in viol[0].detail


def test_forward_dependency_is_allowed(tmp_path):
    """规则 4 正向放行：live2d_builder import core（上游→下游）不算违规。"""
    _write(
        tmp_path / "live2d_builder" / "pipeline.py",
        "from core.workflow import Stage\n",
    )
    result = la.check_rule4_no_reverse_dep(tmp_path)
    assert result.violations == []


# ============================================================
# 任务用例 6：测试文件允许反向依赖（不抓）
# ============================================================

def test_test_files_are_excluded(tmp_path):
    """测试文件允许反向依赖：tests/ 下的 test_x.py 不被抓。"""
    _write(tmp_path / "tests" / "unit" / "test_x.py", "import drivers.bar\n")
    result = la.check_rule4_no_reverse_dep(tmp_path)
    assert result.violations == []


def test_test_filename_in_layer_is_excluded(tmp_path):
    """core/ 下文件名以 test_ 开头的也被跳过（测试可反向依赖做集成）。"""
    _write(tmp_path / "core" / "test_helper.py", "import drivers.bar\n")
    result = la.check_rule4_no_reverse_dep(tmp_path)
    assert result.violations == []


def test_go_test_files_are_excluded(tmp_path):
    """规则 2：Go 测试文件 *_test.go 不参与 import 检查。"""
    _write(
        tmp_path / "api" / "main_test.go",
        'package main\nimport "python/something"\n'
        'import "testing"\n',
    )
    result = la.check_rule2_go_no_python(tmp_path)
    assert result.violations == []


# ============================================================
# 任务用例 7：归档目录不参与检查
# ============================================================

def test_archive_is_excluded(tmp_path):
    """tools/archive/ 归档目录不参与检查（即使含非法 import 也不抓）。"""
    _write(
        tmp_path / "tools" / "archive" / "2026-09" / "probe_x.py",
        "from api.main.go import handler\n",
    )
    result = la.check_rule1_python_no_go_ts(tmp_path)
    assert result.violations == []


def test_pycache_is_excluded(tmp_path):
    """__pycache__ 下的 .py（异常情况）也不参与检查。"""
    _write(
        tmp_path / "core" / "__pycache__" / "stale.py",
        "from api.main.go import handler\n",
    )
    result = la.check_rule1_python_no_go_ts(tmp_path)
    assert result.violations == []


# ============================================================
# 边界：语法错误的 Python 文件不应让脚本崩溃
# ============================================================

def test_syntax_error_file_does_not_crash(tmp_path):
    """规则 4：含语法错误的 Python 文件被跳过，不让脚本崩溃。"""
    _write(tmp_path / "core" / "broken.py", "def bad(:\n")
    result = la.check_rule4_no_reverse_dep(tmp_path)
    # 语法错误文件不计为违规，也不抛异常
    assert all(v.rule_id == "R4" for v in result.violations)


# ============================================================
# 边界：--root scope 解析
# ============================================================

def test_resolve_root_scope_default():
    """不传 --root → (项目根, None)。"""
    root, scope = la._resolve_project_root_and_scope(None)
    assert root == la.PROJECT_ROOT_DEFAULT
    assert scope is None


def test_resolve_root_scope_subdir(tmp_path):
    """传项目根的直接子目录 → (项目根, 该子目录)。"""
    project_root = la.PROJECT_ROOT_DEFAULT
    core = project_root / "core"
    if not core.is_dir():
        return  # 防御：项目结构变化时跳过
    root, scope = la._resolve_project_root_and_scope(str(core))
    assert root == project_root
    assert scope == core


def test_resolve_root_scope_unknown_dir(tmp_path):
    """传任意其它已存在目录 → (该目录, None)。"""
    root, scope = la._resolve_project_root_and_scope(str(tmp_path))
    assert root == tmp_path.resolve()
    assert scope is None
