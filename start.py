#!/usr/bin/env python3
"""
Live2D Master Agent 一键启动器（跨平台）

Usage:
    python start.py              # 并行启动 Go API (8080) + Next.js (3000)
    python start.py --check      # 仅做环境检测，不启动
    python start.py --no-browser # 启动但不自动打开浏览器
    python start.py --no-web     # 只启动 Go API
    python start.py --no-api     # 只启动前端
    python start.py --api-only   # 同 --no-web
    python start.py --web-only   # 同 --no-api

Supports: Windows, macOS, Linux. Requires Python 3.9+.
"""

import os
import re
import sys
import time
import signal
import platform
import argparse
import subprocess
import threading
from pathlib import Path
from collections import deque

PROJECT_ROOT = Path(__file__).parent.resolve()
API_DIR = PROJECT_ROOT / "api"
WEB_DIR = PROJECT_ROOT / "web"
API_URL = "http://localhost:8080"
WEB_URL = "http://localhost:3000"

PYTHON_MIN = (3, 9)
NODE_MIN = (18, 0)
GO_MIN = (1, 21)

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
API_BIN_NAME = "live2d-api.exe" if IS_WINDOWS else "live2d-api"
API_BIN = API_DIR / API_BIN_NAME


class C:
    """终端颜色（非 tty 自动清空，避免管道乱码）。"""
    GREEN = "\033[92m" if sys.stdout.isatty() else ""
    YELLOW = "\033[93m" if sys.stdout.isatty() else ""
    RED = "\033[91m" if sys.stdout.isatty() else ""
    CYAN = "\033[96m" if sys.stdout.isatty() else ""
    BOLD = "\033[1m" if sys.stdout.isatty() else ""
    RESET = "\033[0m" if sys.stdout.isatty() else ""


def banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════╗
║     🚀 Live2D Master Agent 一键启动器                       ║
║     Go API :8080  +  Next.js :3000                          ║
╚══════════════════════════════════════════════════════════╝{C.RESET}
""")


# ============================================================
# 版本解析
# ============================================================

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)")


def parse_node_version(text):
    """从 `node --version` 输出（如 "v24.1.0"）解析 (major, minor)，无法解析返回 None。"""
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def parse_go_version(text):
    """从 `go version` 输出（如 "go version go1.25.1 linux/amd64"）解析 (major, minor)。

    Go 的输出形如 `go1.25.1`，会被同样的正则匹配为 (1, 25)。
    """
    return parse_node_version(text)


_GO_FULL_RE = re.compile(r"go(\d+\.\d+(?:\.\d+)?)")


def format_go_display(raw, ver):
    """从 `go version go1.25.1 linux/amd64` 提取 'go1.25.1' 用于显示。

    提取失败时回退到 go{major}.{minor}。
    """
    if raw:
        m = _GO_FULL_RE.search(raw)
        if m:
            return f"go{m.group(1)}"
    return f"go{ver[0]}.{ver[1]}"


# ============================================================
# 环境检测
# ============================================================

def _run_version_cmd(cmd):
    """运行版本检测命令，合并 stdout+stderr 返回文本，失败返回 None。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "") + (result.stderr or "")


def check_python_version(version_info=None):
    """断言 Python >= 3.9；不满足则 sys.exit(1)。

    可选传入 version_info 以便单元测试；兼容 namedtuple(sys.version_info) 与普通 tuple。
    """
    vi = version_info if version_info is not None else sys.version_info
    try:
        major, minor, micro = vi.major, vi.minor, vi.micro
    except AttributeError:
        major, minor, micro = vi[0], vi[1], vi[2]
    if (major, minor) < PYTHON_MIN:
        print(f"{C.RED}❌ Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ required. "
              f"You have {major}.{minor}.{micro}{C.RESET}")
        print(f"{C.YELLOW}  请升级 Python：https://www.python.org/downloads/{C.RESET}")
        sys.exit(1)
    print(f"{C.GREEN}✓ Python {major}.{minor}.{micro}{C.RESET}")
    return True


def check_environment():
    """检测 Python/Node/Go 运行时版本。返回 True 全部满足，False 任一缺失。"""
    ok = True

    # Python
    try:
        pmajor, pminor, pmicro = (
            sys.version_info.major, sys.version_info.minor, sys.version_info.micro
        )
    except AttributeError:
        pmajor, pminor, pmicro = (
            sys.version_info[0], sys.version_info[1], sys.version_info[2]
        )
    if (pmajor, pminor) < PYTHON_MIN:
        print(f"{C.RED}✗ Python {pmajor}.{pminor}.{pmicro} < "
              f"{PYTHON_MIN[0]}.{PYTHON_MIN[1]}{C.RESET}")
        ok = False
    else:
        print(f"{C.GREEN}✓ Python {pmajor}.{pminor}.{pmicro}{C.RESET}")

    # Node
    node_raw = _run_version_cmd(["node", "--version"])
    node_ver = parse_node_version(node_raw)
    if node_ver is None:
        print(f"{C.RED}✗ Node.js 未安装{C.RESET}")
        print(f"{C.YELLOW}  请安装 Node.js {NODE_MIN[0]}+：https://nodejs.org/{C.RESET}")
        ok = False
    elif node_ver < NODE_MIN:
        node_disp = node_raw.strip() if node_raw else f"v{node_ver[0]}.{node_ver[1]}"
        print(f"{C.RED}✗ Node.js {node_disp} < v{NODE_MIN[0]}.{NODE_MIN[1]}{C.RESET}")
        ok = False
    else:
        node_disp = node_raw.strip() if node_raw else f"v{node_ver[0]}.{node_ver[1]}"
        print(f"{C.GREEN}✓ Node.js {node_disp}{C.RESET}")

    # Go
    go_raw = _run_version_cmd(["go", "version"])
    go_ver = parse_go_version(go_raw)
    if go_ver is None:
        print(f"{C.RED}✗ Go 未安装{C.RESET}")
        print(f"{C.YELLOW}  请安装 Go {GO_MIN[0]}.{GO_MIN[1]}+：https://go.dev/dl/{C.RESET}")
        ok = False
    elif go_ver < GO_MIN:
        print(f"{C.RED}✗ Go {format_go_display(go_raw, go_ver)} < "
              f"go{GO_MIN[0]}.{GO_MIN[1]}{C.RESET}")
        ok = False
    else:
        print(f"{C.GREEN}✓ Go {format_go_display(go_raw, go_ver)}{C.RESET}")

    return ok


def print_environment_report():
    """--check 模式：详细清单（含二进制 / node_modules 检测与修复指引）。返回 True 表示就绪。"""
    print(f"{C.BOLD}━━━ 运行时检测 ━━━{C.RESET}")
    runtime_ok = check_environment()

    print(f"\n{C.BOLD}━━━ 构件检测 ━━━{C.RESET}")
    file_ok = True

    if API_BIN.exists():
        print(f"{C.GREEN}✓ Go 二进制 {API_BIN} [已编译]{C.RESET}")
    else:
        print(f"{C.YELLOW}✗ Go 二进制 {API_BIN} [未编译]{C.RESET}")
        print(f"{C.CYAN}  → 请运行: cd api && go build -o {API_BIN_NAME} .{C.RESET}")
        file_ok = False

    nm = WEB_DIR / "node_modules"
    if nm.exists():
        print(f"{C.GREEN}✓ Next.js {nm} [已安装]{C.RESET}")
    else:
        print(f"{C.YELLOW}✗ Next.js {nm} [未安装]{C.RESET}")
        print(f"{C.CYAN}  → 请运行: cd web && npm install{C.RESET}")
        file_ok = False

    print()
    all_ok = runtime_ok and file_ok
    if all_ok:
        print(f"{C.GREEN}{C.BOLD}✅ 全部就绪，可以启动：python start.py{C.RESET}")
    else:
        print(f"{C.YELLOW}{C.BOLD}⚠ 有缺失项。可一键修复：python install.py{C.RESET}")
    return all_ok


# ============================================================
# 浏览器
# ============================================================

def open_browser(url):
    """跨平台打开浏览器。失败时仅打印提示，不抛异常。"""
    try:
        if IS_WINDOWS:
            os.startfile(url)  # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception as e:  # noqa: BLE001
        print(f"{C.YELLOW}  自动打开浏览器失败：{e}{C.RESET}")
        print(f"{C.YELLOW}  请手动访问：{url}{C.RESET}")


# ============================================================
# 进程封装
# ============================================================

class ManagedProcess:
    """封装一个被管理的子进程：异步读取输出 + 崩溃追踪。"""

    def __init__(self, name, cmd, cwd, prefix):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.prefix = prefix
        self.proc = None
        self.reader = None
        self.lines = deque(maxlen=50)
        self._terminated_by_us = False

    def start(self):
        kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if IS_WINDOWS:
            # Windows 上 npm 是 .cmd，必须 shell=True；同时隐藏控制台窗口
            kwargs["shell"] = True
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            cmd_arg = subprocess.list2cmdline(self.cmd)
        else:
            cmd_arg = self.cmd
        self.proc = subprocess.Popen(cmd_arg, cwd=str(self.cwd), **kwargs)
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        return self

    def _read_loop(self):
        if self.proc is None or self.proc.stdout is None:
            return
        try:
            for line in self.proc.stdout:
                line = line.rstrip("\r\n")
                self.lines.append(line)
                print(f"{self.prefix} {line}", flush=True)
        except Exception:  # noqa: BLE001
            pass

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def terminate(self):
        if self.proc is None:
            return
        self._terminated_by_us = True
        try:
            self.proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    def kill(self):
        if self.proc is None:
            return
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass

    def wait(self, timeout=None):
        if self.proc is None:
            return
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    @property
    def returncode(self):
        return self.proc.returncode if self.proc is not None else None


def build_api_cmd():
    """返回 (cmd_list, cwd)。优先用编译好的二进制，否则 go run。"""
    if API_BIN.exists():
        return [str(API_BIN)], API_DIR
    return ["go", "run", "."], API_DIR


def build_web_cmd():
    """返回 (cmd_list, cwd)。已 build 用 npm start，否则 npm run dev。"""
    if (WEB_DIR / ".next").exists():
        return ["npm", "run", "start"], WEB_DIR
    return ["npm", "run", "dev"], WEB_DIR


def report_crash(p):
    """打印崩溃日志（最后 20 行输出）+ 重启指引。"""
    print(f"\n{C.RED}💥 {p.name} 进程异常退出（exit={p.returncode}）{C.RESET}")
    print(f"{C.RED}  命令：{' '.join(p.cmd)}  (cwd={p.cwd}){C.RESET}")
    tail = list(p.lines)[-20:]
    print(f"{C.RED}  最后 {len(tail)} 行输出：{C.RESET}")
    for line in tail:
        print(f"{C.RED}  {p.prefix} {line}{C.RESET}")
    print(f"{C.YELLOW}  → 重启指引：cd {p.cwd.name} && {' '.join(p.cmd)}{C.RESET}")


def run(start_api=True, start_web=True, open_browser_flag=True):
    """并行启动并管理进程。"""
    procs = {}
    if start_api:
        api_cmd, api_cwd = build_api_cmd()
        procs["api"] = ManagedProcess("Go API", api_cmd, api_cwd, "[api]")
    if start_web:
        web_cmd, web_cwd = build_web_cmd()
        procs["web"] = ManagedProcess("Next.js", web_cmd, web_cwd, "[web]")

    print(f"{C.CYAN}🚀 正在启动：{', '.join(p.name for p in procs.values())}{C.RESET}\n")

    for p in procs.values():
        try:
            p.start()
        except (FileNotFoundError, OSError) as e:
            print(f"{C.RED}❌ 启动 {p.name} 失败：{e}{C.RESET}")
            print(f"{C.YELLOW}  命令：{' '.join(p.cmd)} (cwd={p.cwd}){C.RESET}")
            for other in procs.values():
                if other is not p and other.is_alive():
                    other.terminate()
            sys.exit(1)
        print(f"{C.GREEN}  ✓ {p.name} 已启动 (PID={p.proc.pid}, cwd={p.cwd.name}){C.RESET}")
        print(f"     $ {' '.join(p.cmd)}")

    state = {"shutting_down": False}

    def shutdown(signum=None, frame=None):
        if state["shutting_down"]:
            return
        state["shutting_down"] = True
        print(f"\n{C.YELLOW}🛑 正在停止所有服务...{C.RESET}")
        # 先 terminate 前端，再 terminate 后端，避免后端接收孤儿请求
        if "web" in procs:
            procs["web"].terminate()
        time.sleep(0.3)
        if "api" in procs:
            procs["api"].terminate()
        # 等待 3 秒让进程优雅退出，超时则 kill
        deadline = time.time() + 3
        for p in procs.values():
            remaining = max(0.1, deadline - time.time())
            try:
                p.proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                p.kill()
        print(f"{C.GREEN}✓ 已停止{C.RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (OSError, ValueError):
        # Windows 不支持 SIGTERM 信号注册
        pass

    # 等待 5 秒确认服务就绪
    print(f"\n{C.CYAN}⏳ 等待 5 秒确认服务就绪...{C.RESET}")
    time.sleep(5)

    alive_now = {k: p.is_alive() for k, p in procs.items()}
    crashed = [p for k, p in procs.items()
               if not alive_now[k] and not p._terminated_by_us]

    for p in crashed:
        report_crash(p)

    if all(alive_now.values()):
        print(f"\n{C.GREEN}{C.BOLD}✅ 启动成功！{C.RESET}")
        if "api" in procs and procs["api"].is_alive():
            print(f"   API : {C.CYAN}{API_URL}{C.RESET}")
        if "web" in procs and procs["web"].is_alive():
            print(f"   Web : {C.CYAN}{WEB_URL}{C.RESET}")
        print(f"\n{C.CYAN}按 Ctrl+C 停止所有服务{C.RESET}")
        if open_browser_flag and "web" in procs and procs["web"].is_alive():
            print(f"{C.CYAN}🌐 正在打开浏览器...{C.RESET}")
            open_browser(WEB_URL)
    elif not crashed:
        print(f"{C.YELLOW}⚠ 部分服务已退出（exit code == 0）{C.RESET}")

    # 监控循环：任一进程意外退出则报告，让另一个继续运行
    while True:
        any_alive = False
        for p in procs.values():
            if p.is_alive():
                any_alive = True
            elif not p._terminated_by_us:
                rc = p.returncode
                p._terminated_by_us = True
                if rc is None or rc != 0:
                    report_crash(p)
        if not any_alive:
            print(f"\n{C.YELLOW}所有服务已退出{C.RESET}")
            break
        time.sleep(1)

    shutdown()


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Live2D Master Agent 一键启动器（Go API + Next.js）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python start.py              # 启动 Go API + Next.js\n"
            "  python start.py --check      # 仅检测环境\n"
            "  python start.py --no-browser # 不打开浏览器\n"
            "  python start.py --api-only   # 只启动 Go API\n"
            "  python start.py --web-only   # 只启动前端"
        ),
    )
    parser.add_argument("--check", action="store_true",
                        help="仅做环境检测，不启动")
    parser.add_argument("--no-browser", action="store_true",
                        help="启动后不自动打开浏览器")
    parser.add_argument("--no-web", "--api-only", dest="no_web",
                        action="store_true",
                        help="只启动 Go API（不启动前端）")
    parser.add_argument("--no-api", "--web-only", dest="no_api",
                        action="store_true",
                        help="只启动前端（不启动 Go API）")
    args = parser.parse_args()

    banner()

    # Python 版本硬性要求（脚本本身的最低运行门槛）
    check_python_version()

    if args.check:
        ok = print_environment_report()
        sys.exit(0 if ok else 1)

    # 启动前预检：运行时依赖必须满足
    if not check_environment():
        print(f"\n{C.RED}❌ 环境检测未通过，请先修复后再启动{C.RESET}")
        print(f"{C.YELLOW}  一键修复：python install.py{C.RESET}")
        print(f"{C.YELLOW}  仅检测：python start.py --check{C.RESET}")
        sys.exit(1)

    start_api = not args.no_api
    start_web = not args.no_web
    if not (start_api or start_web):
        print(f"{C.YELLOW}⚠ --no-api 与 --no-web 不能同时指定{C.RESET}")
        sys.exit(1)

    run(
        start_api=start_api,
        start_web=start_web,
        open_browser_flag=not args.no_browser,
    )


if __name__ == "__main__":
    main()