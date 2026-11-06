"""机械守卫：tests/ 里的 subprocess 调用必须显式声明按 UTF-8 解码输出。

为什么要有这条规则（tracker #73）：中文 Windows 的 locale 是 cp936/GBK，
`text=True` 不带 `encoding=` 时 Python 会拿 locale codec 去解子进程输出，
而桥脚本 / native SDK 写的是 UTF-8 字节。任一线程解码失败，reader thread
抛 UnicodeDecodeError，`completed.stdout` 回来就是 None，症状看着像
「子进程没输出」（AttributeError: 'NoneType' object has no attribute
'find'），把真正的原因藏掉。

产品侧早就把这条规则固化了：drivers/live2d_runtime/moc3_verify.py 的
_spawn 用 encoding="utf-8", errors="replace"；tests/unit/test_moc3_verify.py
::test_subprocess_output_is_decoded_as_utf8 断言 text=True 不得使用。
本文件把同一条规则机械地套在 tests/ 自己身上。

覆盖范围（只此，别扩大也别缩小）：仓库根的 tests/ 整棵子树，含
tests/integration/、tests/unit/、tests/e2e/ 和 tests/conftest.py。
tests/ 之外同类的 8 处（install.py、start.py、scripts/build_desktop_bundle.py、
scripts/repeat_verify.py 两处、scripts/smoke_e2e.py 两处、core/gpu_probe.py）
是另行跟踪的工作项，故意不在本守卫射程内 —— 现在扩进去会让守卫带着 8 条
已知违规变红，把「谁改坏了 tests/」和「那批还没修」混成一条噪音。
"""
import ast
import os
from pathlib import Path

# 本文件在 tests/unit/，所以 tests/ 是 parent.parent，仓库根再往上一级。
# 曾经写成 parent / parent.parent（即 tests/unit 与 tests），守卫就只在
# tests/unit 里走路，tests/integration 那 6 处全在射程外：全量回退也不会红。
TESTS_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_ROOT.parent

# 会按 locale 解码子进程输出的 subprocess 入口（只认 subprocess.<name> 写法）
_SUBPROCESS_FUNCS = {"run", "check_output", "check_call", "call", "Popen"}
# text= 的两个写法（universal_newlines 是旧拼写，等价）
_TEXT_KEYWORDS = {"text", "universal_newlines"}


def _subprocess_call_name(node: ast.Call):
    """subprocess.run(...) → 'run'；其它（含裸 run(...)、os.run(...)）→ None。"""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    base = func.value
    if not (isinstance(base, ast.Name) and base.id == "subprocess"):
        return None
    return func.attr if func.attr in _SUBPROCESS_FUNCS else None


def _asks_for_text_mode(node: ast.Call) -> bool:
    """text=True / universal_newlines=True；非字面量的 text=<flag> 也一并拦。"""
    for kw in node.keywords:
        if kw.arg not in _TEXT_KEYWORDS:
            continue
        value = kw.value
        if isinstance(value, ast.Constant) and value.value is False:
            continue
        return True
    return False


def _label(path: Path) -> str:
    """相对项目根的显示名；跨盘符或传相对路径时退回原路径，守卫不该因此崩。"""
    try:
        return os.path.relpath(str(path), str(PROJECT_ROOT)).replace(os.sep, "/")
    except ValueError:
        return str(path)


def _violations_in(path: Path) -> list:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{_label(path)}: 无法按 UTF-8 读取（{exc}）"]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # 解析不动就报告，别静默放行
        return [f"{_label(path)}: 无法解析（{exc}）"]
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _subprocess_call_name(node)
        if name is None or not _asks_for_text_mode(node):
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        found.append(
            '{0}:{1} subprocess.{2} 用了 text=True 却没给 encoding= '
            '—— 请加 encoding="utf-8", errors="replace"'.format(
                _label(path), node.lineno, name)
        )
    return found


def _python_files(root: Path) -> list:
    """root 下要扫的 .py 文件（排序、跳过 __pycache__）。

    单独拆出来是给 test_guard_actually_covers_the_tests 用的：光看
    「违规数 = 0」分不出「扫干净了」和「压根没扫到」。
    """
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def scan(root: Path) -> list:
    """扫 root 下所有 .py，返回违规描述列表。"""
    violations = []
    for path in _python_files(root):
        violations.extend(_violations_in(path))
    return violations


def test_tests_decode_subprocess_output_explicitly():
    """tests/ 里不允许出现「按 locale 解码子进程输出」的调用。"""
    violations = scan(TESTS_ROOT)
    assert violations == [], (
        "子进程输出必须显式按 UTF-8 解码（中文 Windows 的 locale 是 cp936，\n"
        "UTF-8 日志解码失败会让 stdout 变成 None）：\n" + "\n".join(violations)
    )


def test_guard_actually_covers_the_files_that_matter():
    """守卫自证扫描范围：不能靠「0 条违规」冒充安全。

    真实缺陷（修复轮 1 发现的）：TESTS_ROOT 一度写成
    Path(__file__).resolve().parent，也就是 tests/unit。于是
    test_tests_decode_subprocess_output_explicitly 只在 tests/unit 里走路，
    本任务修好的 tests/integration 那 6 处全在射程外 —— 把它们整体回退，
    守卫照样绿，这是最坏的假保护。下面这几条把「悄悄收窄」钉成红：
    根必须等于由本文件位置推出的 tests/，且 integration/ 与 unit/ 都得真在
    扫描清单里。
    """
    here = Path(__file__).resolve()

    # 1) 扫描锚点：root 就是仓库的 tests/，PROJECT_ROOT 就是仓库根
    #    （这两条是绝对锚，后面按 TESTS_ROOT 派生的检查才不会空转通过）
    assert TESTS_ROOT == here.parent.parent, TESTS_ROOT
    assert TESTS_ROOT.is_dir() and TESTS_ROOT.name == "tests", TESTS_ROOT
    assert PROJECT_ROOT == TESTS_ROOT.parent, PROJECT_ROOT
    assert (PROJECT_ROOT / "tests").resolve() == TESTS_ROOT

    labels = [_label(p) for p in _python_files(TESTS_ROOT)]

    # 2) 该扫的两头都必须真扫到：integration（被修的 6 处住这里）和 unit（守卫自己）
    integration = sorted(x for x in labels if x.startswith("tests/integration/"))
    unit = sorted(x for x in labels if x.startswith("tests/unit/"))
    assert integration, "扫描没走 tests/integration/ —— 守卫又被收窄了"
    assert unit, "扫描没走 tests/unit/ —— 守卫范围不对"

    # 3) tests/ 下每个放 .py 的目录都不许整段漏掉（conftest.py 也在射程内）
    assert "tests/conftest.py" in labels, labels
    assert _label(here) in labels, "守卫没扫到自己"
    dirs_with_py = sorted(
        d.relative_to(TESTS_ROOT).as_posix()
        for d in TESTS_ROOT.rglob("*")
        if d.is_dir() and "__pycache__" not in d.parts and any(d.glob("*.py"))
    )

    def covered(relative_dir: str) -> bool:
        needle = "tests/" + relative_dir
        return any(x == needle or x.startswith(needle + "/") for x in labels)

    uncovered = [d for d in dirs_with_py if not covered(d)]
    assert uncovered == [], "这些目录里的 .py 一个都没被扫到：" + ", ".join(uncovered)


def test_guard_catches_the_bad_idiom(tmp_path):
    """守卫自身不能是空转：坏写法必须被抓。"""
    (tmp_path / "test_bad.py").write_text(
        "import subprocess\n"
        "p = subprocess.run(['x'], capture_output=True, text=True, timeout=5)\n"
        "q = subprocess.check_output(['y'], universal_newlines=True)\n"
        "r = subprocess.Popen(['z'], stdout=-1, text=True)\n",
        encoding="utf-8",
    )
    found = scan(tmp_path)
    assert len(found) == 3, found
    assert all("encoding=" in line for line in found), found


def test_guard_passes_the_good_idiom(tmp_path):
    """显式 encoding= 或 text=False 或字节模式都放行。"""
    (tmp_path / "test_good.py").write_text(
        "import subprocess\n"
        "p = subprocess.run(['x'], capture_output=True, timeout=5,\n"
        "                 encoding='utf-8', errors='replace')\n"
        "q = subprocess.run(['y'], capture_output=True, text=True, encoding='utf-8')\n"
        "r = subprocess.run(['z'], capture_output=True, text=False)\n"
        "s = subprocess.run(['w'], capture_output=True)\n"
        "os.run(['v'], text=True)\n"
        "runner.run(['u'], text=True)\n",
        encoding="utf-8",
    )
    assert scan(tmp_path) == []
