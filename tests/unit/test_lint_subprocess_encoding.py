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
"""
import ast
import os
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
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


def scan(root: Path) -> list:
    """扫 root 下所有 .py，返回违规描述列表。"""
    violations = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        violations.extend(_violations_in(path))
    return violations


def test_tests_decode_subprocess_output_explicitly():
    """tests/ 里不允许出现「按 locale 解码子进程输出」的调用。"""
    violations = scan(TESTS_ROOT)
    assert violations == [], (
        "子进程输出必须显式按 UTF-8 解码（中文 Windows 的 locale 是 cp936，\n"
        "UTF-8 日志解码失败会让 stdout 变成 None）：\n" + "\n".join(violations)
    )


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
