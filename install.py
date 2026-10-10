#!/usr/bin/env python3
"""
Live2D Master Agent v0.10.0 - One-Click Installer

Usage:
    python install.py              # Full install (core + desktop pet)
    python install.py --minimal    # Core only
    python install.py --ai         # Core + AI models (large download)
    python install.py --dev        # Development install with test tools

Supports: Windows, macOS, Linux. Requires Python 3.9+.
"""

import os
import re
import sys
import subprocess
import platform
import shutil
from pathlib import Path
from importlib.metadata import version as _pkg_version, PackageNotFoundError


PROJECT_ROOT = Path(__file__).parent.resolve()
PYTHON = sys.executable or "python3"

PYTHON_MIN = (3, 9)
NODE_MIN = (18, 0)
GO_MIN = (1, 21)

FAQ_PATH = PROJECT_ROOT / "docs" / "FAQ.md"

# Terminal colors
class C:
    GREEN = "\033[92m" if sys.stdout.isatty() else ""
    YELLOW = "\033[93m" if sys.stdout.isatty() else ""
    RED = "\033[91m" if sys.stdout.isatty() else ""
    CYAN = "\033[96m" if sys.stdout.isatty() else ""
    BOLD = "\033[1m" if sys.stdout.isatty() else ""
    RESET = "\033[0m" if sys.stdout.isatty() else ""


def _env_timeout(name, default):
    """读取子进程超时（秒）。环境变量设为 0 表示不限制（恢复旧行为）。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
        return val if val > 0 else None
    except ValueError:
        print(f"{C.YELLOW}⚠ 忽略无效的 {name}={raw!r}，使用默认值 {default}s{C.RESET}")
        return default


# 子进程超时：安装/构建命令必须有上限以防网络挂起导致永久阻塞，
# 但默认值要足够宽松（大体积包/慢网络），并允许通过环境变量覆盖。
STEP_TIMEOUT = _env_timeout("LIVE2D_STEP_TIMEOUT", 1800)              # 通用步骤 30 分钟
PIP_TIMEOUT = _env_timeout("LIVE2D_PIP_TIMEOUT", 1800)                # pip 安装 30 分钟
MODEL_DOWNLOAD_TIMEOUT = _env_timeout("LIVE2D_MODEL_DOWNLOAD_TIMEOUT", 3600)  # 模型下载 1 小时


# ============================================================
# 版本解析（与 start.py 保持一致的语义）
# ============================================================

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)")
_GO_FULL_RE = re.compile(r"go(\d+\.\d+(?:\.\d+)?)")


def parse_node_version(text):
    """从 `node --version` 输出（如 "v24.1.0"）解析 (major, minor)，失败返回 None。"""
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def parse_go_version(text):
    """从 `go version` 输出（如 "go version go1.25.1 linux/amd64"）解析 (major, minor)。"""
    return parse_node_version(text)


def format_go_display(raw, ver):
    """从 `go version go1.25.1 linux/amd64` 提取 'go1.25.1' 用于显示；失败回退 go{maj}.{min}。"""
    if raw:
        m = _GO_FULL_RE.search(raw)
        if m:
            return f"go{m.group(1)}"
    return f"go{ver[0]}.{ver[1]}"


def _run_version_cmd(cmd):
    """运行版本检测命令，合并 stdout+stderr 返回文本，失败返回 None。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "") + (result.stderr or "")


def get_pkg_version(name):
    """通过 importlib.metadata 获取已安装包的版本字符串，未安装返回 None。"""
    try:
        return _pkg_version(name)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


# ============================================================
# 安装状态（累计成功 / 失败 / 跳过步骤，用于最终总结）
# ============================================================

class InstallState:
    def __init__(self, dry_run=False, yes=False):
        self.dry_run = dry_run
        self.yes = yes
        self.success = []
        self.failed = []
        self.skipped = []
        self.abort = False

    def mark_success(self, name):
        self.success.append(name)

    def mark_failed(self, name):
        self.failed.append(name)

    def mark_skipped(self, name):
        self.skipped.append(name)


def print_header():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════╗
║       🎭 Live2D Master Agent v0.10.0 - Installer           ║
║       AI Character → Live2D Model → Desktop Pet          ║
╚══════════════════════════════════════════════════════════╝{C.RESET}
""")


def print_check_header():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════╗
║     📦 Live2D Master Agent 环境检测                          ║
╚══════════════════════════════════════════════════════════╝{C.RESET}
""")


def check_python(strict=True):
    """检测 Python 版本是否满足 PYTHON_MIN；strict=True 时不满足直接 sys.exit(1)。"""
    try:
        major, minor, micro = (
            sys.version_info.major, sys.version_info.minor, sys.version_info.micro
        )
    except AttributeError:
        major, minor, micro = (
            sys.version_info[0], sys.version_info[1], sys.version_info[2]
        )
    if (major, minor) < PYTHON_MIN:
        print(f"{C.RED}✗ Python {major}.{minor}.{micro} (< {PYTHON_MIN[0]}.{PYTHON_MIN[1]}){C.RESET}")
        if strict:
            print(f"{C.YELLOW}  请升级 Python：https://www.python.org/downloads/{C.RESET}")
            sys.exit(1)
        return False
    print(f"{C.GREEN}✓ Python {major}.{minor}.{micro} (≥{PYTHON_MIN[0]}.{PYTHON_MIN[1]}){C.RESET}")
    return True


def check_os():
    """Detect OS and print info."""
    system = platform.system()
    print(f"{C.GREEN}✓ OS: {system} {platform.release()}{C.RESET}")

    if system == "Windows":
        print(f"{C.YELLOW}  Note: On Windows, ensure you have Visual C++ Redistributable{C.RESET}")
        print(f"{C.YELLOW}  https://aka.ms/vs/17/release/vc_redist.x64.exe{C.RESET}")

    return system


def check_node():
    """检测 Node.js / npm 版本。返回 (ok, version_str)。"""
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node:
        print(f"{C.YELLOW}✗ Node.js 未安装 (≥v{NODE_MIN[0]}.{NODE_MIN[1]}){C.RESET}")
        print(f"{C.CYAN}  → 下载: https://nodejs.org/{C.RESET}")
        return False, None
    if not npm:
        print(f"{C.YELLOW}✗ npm 未随 Node.js 一起安装{C.RESET}")
        print(f"{C.CYAN}  → 请重装 Node.js: https://nodejs.org/{C.RESET}")
        return False, None
    raw = _run_version_cmd([node, "--version"])
    ver = parse_node_version(raw)
    if ver is None:
        print(f"{C.YELLOW}⚠ Node.js 版本无法解析，web UI 不可用{C.RESET}")
        return False, None
    disp = raw.strip() if raw else f"v{ver[0]}.{ver[1]}"
    if ver < NODE_MIN:
        print(f"{C.RED}✗ Node.js {disp} (< v{NODE_MIN[0]}.{NODE_MIN[1]}){C.RESET}")
        print(f"{C.CYAN}  → 请升级: https://nodejs.org/{C.RESET}")
        return False, disp
    print(f"{C.GREEN}✓ Node.js {disp} (≥v{NODE_MIN[0]}){C.RESET}")
    return True, disp


def check_go():
    """检测 Go 版本。返回 (ok, version_str)。"""
    go = shutil.which("go")
    if not go:
        print(f"{C.YELLOW}✗ Go 未安装 (≥go{GO_MIN[0]}.{GO_MIN[1]}){C.RESET}")
        print(f"{C.CYAN}  → 下载: https://go.dev/dl/{C.RESET}")
        return False, None
    raw = _run_version_cmd([go, "version"])
    ver = parse_go_version(raw)
    if ver is None:
        print(f"{C.YELLOW}⚠ Go 版本无法解析，API server 不可用{C.RESET}")
        return False, None
    disp = format_go_display(raw, ver)
    if ver < GO_MIN:
        print(f"{C.RED}✗ Go {disp} (< go{GO_MIN[0]}.{GO_MIN[1]}){C.RESET}")
        print(f"{C.CYAN}  → 请升级: https://go.dev/dl/{C.RESET}")
        return False, disp
    print(f"{C.GREEN}✓ Go {disp} (≥go{GO_MIN[0]}.{GO_MIN[1]}){C.RESET}")
    return True, disp


def check_ffmpeg():
    """可选：检测 ffmpeg。返回 (ok, version_str)。仅视频导出需要。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, None
    raw = _run_version_cmd([ffmpeg, "-version"])
    if not raw:
        return True, "ffmpeg"
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    return True, lines[0] if lines else "ffmpeg"


# ============================================================
# 步骤失败处理（打印具体错误 + 修复指引 + 询问是否继续）
# ============================================================

def _ask_continue():
    """询问是否继续后续步骤。无法交互时默认继续，避免在管道/CI 卡死。"""
    if not sys.stdin or not sys.stdin.isatty():
        print(f"{C.YELLOW}  （非交互环境，默认继续后续步骤）{C.RESET}")
        return True
    try:
        ans = input(f"{C.YELLOW}  是否继续后续步骤？[Y/n]{C.RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("", "y", "yes")


def handle_step_failure(step_desc, error, state=None):
    """打印具体错误 + 修复指引 + 询问是否继续。返回 True=继续, False=中止。"""
    print(f"{C.RED}❌ 步骤失败：{step_desc}{C.RESET}")
    rc = getattr(error, "returncode", None)
    out = getattr(error, "output", None) or getattr(error, "stdout", None)
    err = getattr(error, "stderr", None)
    if rc is not None:
        print(f"{C.RED}  退出码：{rc}{C.RESET}")
    if out:
        print(f"{C.RED}  stdout: {(str(out) or '').strip()[:500]}{C.RESET}")
    if err:
        print(f"{C.RED}  stderr: {(str(err) or '').strip()[:500]}{C.RESET}")
    elif isinstance(error, Exception) and rc is None and not out:
        print(f"{C.RED}  错误：{error!r}{C.RESET}")
    if FAQ_PATH.exists():
        print(f"{C.YELLOW}  → 修复指引：{FAQ_PATH}{C.RESET}")
    else:
        print(f"{C.YELLOW}  → 修复指引：参见项目 README.md 或官方文档{C.RESET}")
    if state is not None and state.yes:
        print(f"{C.YELLOW}  --yes 模式：遇到失败即中止{C.RESET}")
        return False
    return _ask_continue()


def run_step(name, cmd, cwd=None, env=None, state=None):
    """运行一个步骤（cmd 为 list）。dry-run 时仅打印；失败时统一处理。返回 True/False。"""
    if state is not None and state.dry_run:
        env_hint = f" (cwd={cwd})" if cwd else ""
        print(f"{C.CYAN}[dry-run] $ {' '.join(str(c) for c in cmd)}{env_hint}{C.RESET}")
        return True
    try:
        subprocess.run(cmd, cwd=cwd, env=env, check=True, timeout=STEP_TIMEOUT)
        return True
    except subprocess.TimeoutExpired:
        print(f"{C.RED}❌ 步骤超时（>{STEP_TIMEOUT}s）：{name}{C.RESET}")
        print(f"{C.YELLOW}  可用环境变量 LIVE2D_STEP_TIMEOUT 调整上限（0=不限）{C.RESET}")
        if state is not None and state.yes:
            return False
        return False
    except subprocess.CalledProcessError as e:
        handle_step_failure(name, e, state)
        return False
    except FileNotFoundError as e:
        handle_step_failure(name, e, state)
        return False


def run_pip(args, description="", state=None):
    """Run pip install with error handling."""
    if description:
        print(f"\n{C.CYAN}📦 {description}{C.RESET}")

    cmd = [PYTHON, "-m", "pip", "install", "--upgrade"] + args
    if state is not None and state.dry_run:
        print(f"{C.CYAN}[dry-run] $ {' '.join(cmd)}{C.RESET}")
        return True
    try:
        subprocess.run(cmd, check=True, timeout=PIP_TIMEOUT)
        return True
    except subprocess.TimeoutExpired:
        print(f"{C.RED}❌ pip 安装超时（>{PIP_TIMEOUT}s）：{' '.join(args)}{C.RESET}")
        print(f"{C.YELLOW}  网络过慢时可用环境变量 LIVE2D_PIP_TIMEOUT 调大上限（0=不限）{C.RESET}")
        return False
    except subprocess.CalledProcessError as e:
        handle_step_failure("pip install " + " ".join(args), e, state)
        return False


def install_core(state=None):
    """Install core Python dependencies."""
    print(f"\n{C.BOLD}━━━ Step 1/5: Core Python Dependencies ━━━{C.RESET}")
    if state is None:
        state = InstallState()
    step_name = "Core Python Dependencies"

    run_pip(["pip"], "Upgrading pip...", state)

    req_file = PROJECT_ROOT / "requirements.txt"
    if not req_file.exists():
        print(f"{C.YELLOW}⚠ requirements.txt 不存在，跳过{C.RESET}")
        state.mark_skipped(step_name)
        return True

    if state.dry_run:
        print(f"\n{C.CYAN}📦 Installing from requirements.txt...{C.RESET}")
        print(f"{C.CYAN}[dry-run] $ {PYTHON} -m pip install -r {req_file}{C.RESET}")
        state.mark_success(step_name)
        return True

    print(f"\n{C.CYAN}📦 Installing from requirements.txt...{C.RESET}")
    try:
        subprocess.run(
            [PYTHON, "-m", "pip", "install", "-r", str(req_file)],
            check=True,
            timeout=PIP_TIMEOUT,
        )
        print(f"{C.GREEN}✓ Core dependencies installed{C.RESET}")
        state.mark_success(step_name)
        return True
    except subprocess.TimeoutExpired:
        print(f"{C.RED}❌ pip install -r requirements.txt 超时（>{PIP_TIMEOUT}s）{C.RESET}")
        print(f"{C.YELLOW}  可用环境变量 LIVE2D_PIP_TIMEOUT 调大上限（0=不限）{C.RESET}")
        state.abort = True
        state.mark_failed(step_name)
        return False
    except subprocess.CalledProcessError as e:
        if not handle_step_failure("pip install -r requirements.txt", e, state):
            state.abort = True
            state.mark_failed(step_name)
            return False
        print(f"{C.YELLOW}  尝试逐个安装核心包...{C.RESET}")
        core_pkgs = [
            "Pillow>=10.0.0", "numpy>=1.24.0", "requests>=2.32.3",
            "urllib3>=2.2.2", "httpx>=0.24.0", "aiohttp>=3.10.11",
            "psd-tools>=1.9.0", "scipy>=1.10.0", "scikit-learn>=1.3.0",
            "cryptography>=42.0.0", "rich>=13.0.0",
            "opencv-python-headless>=4.9.0.80", "onnxruntime>=1.14.0",
            "aiofiles>=23.0", "websockets>=12.0",
        ]
        if run_pip(core_pkgs, "Installing individual packages...", state):
            state.mark_success(step_name + " (individual)")
            return True
        state.mark_failed(step_name)
        return False


def install_desktop_pet(state=None):
    """Install desktop pet dependencies."""
    print(f"\n{C.BOLD}━━━ Step 2/5: Desktop Pet Dependencies ━━━{C.RESET}")
    if state is None:
        state = InstallState()
    step_name = "Desktop Pet Dependencies"

    if sys.version_info >= (3, 14):
        pkgs = ["pygame-ce>=2.5.0"]
    else:
        pkgs = ["pygame>=2.5.0"]

    pkgs.append("mediapipe>=0.10.0")
    pkgs.extend(["sounddevice>=0.4.6", "soundfile>=0.12.0"])
    pkgs.append("edge-tts>=6.1.0")
    pkgs.append("rembg[cpu]>=2.0.0")

    if run_pip(pkgs, "Installing desktop pet, tracking, and TTS packages...", state):
        if not state.dry_run:
            print(f"{C.GREEN}✓ Desktop pet dependencies installed{C.RESET}")
        state.mark_success(step_name)
        return True
    state.mark_failed(step_name)
    return False


def install_ai_models(state=None):
    """Install optional AI model dependencies (large downloads)."""
    print(f"\n{C.BOLD}━━━ Step 3/5: AI Model Dependencies ━━━{C.RESET}")
    if state is None:
        state = InstallState()
    step_name = "AI Model Dependencies"
    print(f"{C.YELLOW}⚠ This downloads PyTorch and other large packages (2-5 GB){C.RESET}")

    pkgs = [
        "torch>=2.0.0",
        "torchvision>=0.15.0",
        "transformers>=4.30.0",
        "segment-anything>=1.0",
        "huggingface-hub>=0.17.0",
    ]

    pip_ok = run_pip(pkgs, "Installing PyTorch, transformers, SAM...", state)
    if pip_ok:
        if not state.dry_run:
            print(f"{C.GREEN}✓ AI model dependencies installed{C.RESET}")
        state.mark_success(step_name)
    else:
        state.mark_failed(step_name)

    if state.dry_run:
        print(f"\n{C.CYAN}🤖 [dry-run] 下载 SAM 模型...{C.RESET}")
        print(f"{C.CYAN}[dry-run] $ {PYTHON} scripts/download_models.py --model sam{C.RESET}")
    else:
        print(f"\n{C.CYAN}🤖 Downloading SAM model (if not cached)...{C.RESET}")
        try:
            sam_script = PROJECT_ROOT / "scripts" / "download_models.py"
            if sam_script.exists():
                subprocess.run(
                    [PYTHON, str(sam_script), "--model", "sam"],
                    check=False,
                    timeout=MODEL_DOWNLOAD_TIMEOUT,
                )
        except subprocess.TimeoutExpired:
            print(
                f"{C.YELLOW}⚠ SAM 模型下载超时（>{MODEL_DOWNLOAD_TIMEOUT}s），"
                f"可稍后运行 python scripts/download_models.py --model sam 重试，"
                f"或用 LIVE2D_MODEL_DOWNLOAD_TIMEOUT 调大上限{C.RESET}"
            )
        except Exception as e:
            print(f"{C.YELLOW}⚠ Model download skipped: {e}{C.RESET}")

    return pip_ok


def install_web(has_node: bool, state=None):
    """Install web frontend dependencies."""
    print(f"\n{C.BOLD}━━━ Step 4/5: Web Frontend ━━━{C.RESET}")
    if state is None:
        state = InstallState()
    step_name = "Web Frontend"

    if not has_node:
        print(f"{C.YELLOW}⚠ 跳过 - Node.js 不可用{C.RESET}")
        state.mark_skipped(step_name)
        return False

    web_dir = PROJECT_ROOT / "web"
    if not web_dir.exists():
        print(f"{C.YELLOW}⚠ Web 目录不存在，跳过{C.RESET}")
        state.mark_skipped(step_name)
        return False

    if state.dry_run:
        print(f"{C.CYAN}📦 Installing npm packages...{C.RESET}")
        print(f"{C.CYAN}[dry-run] $ npm install (cwd={web_dir}){C.RESET}")
        state.mark_success(step_name)
        return True

    print(f"{C.CYAN}📦 Installing npm packages...{C.RESET}")
    if run_step(step_name, ["npm", "install"], cwd=str(web_dir), state=state):
        print(f"{C.GREEN}✓ Web dependencies installed{C.RESET}")
        state.mark_success(step_name)
        return True
    state.mark_failed(step_name)
    return False


def install_api(has_go: bool, state=None):
    """Build Go API server."""
    print(f"\n{C.BOLD}━━━ Step 5/5: Go API Server ━━━{C.RESET}")
    if state is None:
        state = InstallState()
    step_name = "Go API Server"

    if not has_go:
        print(f"{C.YELLOW}⚠ 跳过 - Go 不可用{C.RESET}")
        state.mark_skipped(step_name)
        return False

    api_dir = PROJECT_ROOT / "api"
    if not api_dir.exists():
        print(f"{C.YELLOW}⚠ API 目录不存在，跳过{C.RESET}")
        state.mark_skipped(step_name)
        return False

    api_bin_name = "live2d-api.exe" if platform.system() == "Windows" else "live2d-api"

    if state.dry_run:
        print(f"{C.CYAN}📦 Downloading Go dependencies...{C.RESET}")
        print(f"{C.CYAN}[dry-run] $ go mod tidy (cwd={api_dir}){C.RESET}")
        print(f"{C.CYAN}🔨 Building API server (low-memory mode)...{C.RESET}")
        print(f"{C.CYAN}[dry-run] $ go build -p 1 -o {api_bin_name} . (cwd={api_dir}, GOMAXPROCS=1){C.RESET}")
        state.mark_success(step_name)
        return True

    print(f"{C.CYAN}📦 Downloading Go dependencies...{C.RESET}")
    if not run_step(step_name + " (go mod tidy)", ["go", "mod", "tidy"],
                    cwd=str(api_dir), state=state):
        state.mark_failed(step_name)
        return False
    print(f"{C.CYAN}🔨 Building API server (low-memory mode)...{C.RESET}")
    build_env = os.environ.copy()
    build_env['GOMAXPROCS'] = '1'
    if run_step(step_name + " (go build)",
                ["go", "build", "-p", "1", "-o", api_bin_name, "."],
                cwd=str(api_dir), env=build_env, state=state):
        print(f"{C.GREEN}✓ API server built{C.RESET}")
        state.mark_success(step_name)
        return True
    state.mark_failed(step_name)
    return False


def create_env_file():
    """Create .env from .env.example if not exists."""
    env_example = PROJECT_ROOT / ".env.example"
    env_file = PROJECT_ROOT / ".env"

    if env_file.exists():
        print(f"\n{C.GREEN}✓ .env already exists{C.RESET}")
        return

    if env_example.exists():
        shutil.copy2(env_example, env_file)
        print(f"\n{C.GREEN}✓ Created .env from .env.example{C.RESET}")
        print(f"{C.YELLOW}  Edit .env to add your API keys (optional){C.RESET}")
    else:
        env_file.write_text("""# Live2D Master Agent Configuration
# Copy this to .env and edit

# API Keys (optional - Pollinations is free, no key needed)
# ARK_API_KEY=your-volcengine-key
# SENSENOVA_API_KEY=your-sensenova-key

# LLM Configuration (optional, for chat features)
# OPENAI_API_KEY=your-openai-key
# OPENAI_BASE_URL=https://api.openai.com/v1
# LLM_MODEL=gpt-4o-mini

# TTS Voice (edge-tts, free)
TTS_VOICE=zh-CN-XiaoxiaoNeural

# Server
GO_API_HOST=0.0.0.0
GO_API_PORT=8080

# Output
OUTPUT_DIR=./output
""", encoding="utf-8")
        print(f"\n{C.GREEN}✓ Created default .env{C.RESET}")


def create_directories():
    """Create necessary output directories."""
    dirs = [
        "assets/characters",
        "assets/output",
        "assets/models",
        "output",
        "logs",
    ]
    for d in dirs:
        path = PROJECT_ROOT / d
        if path.exists() and not path.is_dir():
            path.unlink()
        path.mkdir(parents=True, exist_ok=True)
    print(f"{C.GREEN}✓ Directories created{C.RESET}")


def verify_installation():
    """Verify the installation works."""
    print(f"\n{C.BOLD}━━━ Verification ━━━{C.RESET}")

    errors = []

    # Test Python imports
    test_imports = [
        ("PIL", "Pillow"),
        ("numpy", "NumPy"),
        ("scipy", "SciPy"),
        ("sklearn", "scikit-learn"),
        ("cv2", "OpenCV"),
        ("psd_tools", "psd-tools"),
        ("cryptography", "cryptography"),
        ("httpx", "httpx"),
        ("aiohttp", "aiohttp"),
    ]

    for module, name in test_imports:
        try:
            __import__(module)
            print(f"  {C.GREEN}✓ {name}{C.RESET}")
        except ImportError:
            print(f"  {C.RED}✗ {name} (not installed){C.RESET}")
            errors.append(name)

    # Test optional imports
    optional = [
        ("pygame", "Pygame (desktop pet)"),
        ("mediapipe", "MediaPipe (face tracking)"),
        ("sounddevice", "sounddevice (audio)"),
        ("edge_tts", "edge-tts (TTS)"),
        ("rembg", "rembg (background removal)"),
    ]

    print()
    for module, name in optional:
        try:
            __import__(module)
            print(f"  {C.GREEN}✓ {name}{C.RESET}")
        except (ImportError, OSError) as e:
            print(f"  {C.YELLOW}⚠ {name} (optional, {e.__class__.__name__}){C.RESET}")

    # Test core module
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from core.version import __version__
        print(f"\n  {C.GREEN}✓ Live2D Master Agent v{__version__} ready!{C.RESET}")
    except Exception as e:
        print(f"\n  {C.RED}✗ Core module error: {e}{C.RESET}")
        errors.append("core")

    return len(errors) == 0


def print_next_steps(has_node, has_go):
    """Print quick start instructions."""
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════╗
║                   ✅ Installation Complete!               ║
╚══════════════════════════════════════════════════════════╝{C.RESET}

{C.BOLD}🚀 Quick Start:{C.RESET}

  1. Generate a character (free, no API key needed):
     {C.GREEN}python -m core.workflow "蓝发猫耳少女，白色背景" --deploy-desktop{C.RESET}

  2. Run the desktop pet:
     {C.GREEN}python -m drivers.desktop_pet.runner{C.RESET}

  3. Start the web workbench:
     {C.GREEN}cd web && npm run dev{C.RESET}
     Open http://localhost:3000
""")

    if has_go:
        print(f"""  4. Start the API server:
     {C.GREEN}cd api && ./live2d-api{C.RESET}
     API at http://localhost:8080
""")

    print(f"""  5. Interactive mode:
     {C.GREEN}python -m core.cli{C.RESET}

{C.BOLD}📚 Documentation:{C.RESET}
  See README.md, docs/QUICKSTART.md, docs/USER_GUIDE.md

{C.YELLOW}Note: Default uses Pollinations free image generation.
For higher quality, add API keys in .env file.{C.RESET}
""")


# ============================================================
# --check 子命令：仅检测，不安装
# ============================================================

# (展示名, PyPI 包名, 是否必填)
_PYTHON_DEPS = [
    ("Pillow", "Pillow", True),
    ("numpy", "numpy", True),
    ("scipy", "scipy", True),
    ("scikit-learn", "scikit-learn", True),
    ("opencv-python-headless", "opencv-python-headless", True),
    ("psd-tools", "psd-tools", True),
    ("cryptography", "cryptography", True),
    ("httpx", "httpx", True),
    ("aiohttp", "aiohttp", True),
    ("aiofiles", "aiofiles", True),
    ("websockets", "websockets", True),
    ("rich", "rich", True),
    ("onnxruntime", "onnxruntime", True),
    ("pygame-ce", "pygame-ce", False),
    ("mediapipe", "mediapipe", False),
    ("sounddevice", "sounddevice", False),
    ("edge-tts", "edge-tts", False),
    ("rembg", "rembg", False),
]


def check_python_deps_report():
    """打印 Python 依赖清单（用于 --check）。返回 True=必填项全部满足。"""
    required_ok = True
    for display, pkg, required in _PYTHON_DEPS:
        ver = get_pkg_version(pkg)
        if ver:
            tag = "" if required else "（可选）"
            print(f"{C.GREEN}✓ {display} {ver}{tag}{C.RESET}")
        else:
            if required:
                print(f"{C.RED}✗ {display} 缺失{C.RESET}")
                print(f"{C.CYAN}  → 运行: pip install {pkg}{C.RESET}")
                required_ok = False
            else:
                print(f"{C.YELLOW}⚠ {display} 未安装（可选）{C.RESET}")
                print(f"{C.CYAN}  → 运行: pip install {pkg}{C.RESET}")
    return required_ok


def check_artifacts_report():
    """打印构件清单（用于 --check）。返回 True=关键构件就绪。"""
    ok = True
    api_bin_name = "live2d-api.exe" if platform.system() == "Windows" else "live2d-api"
    api_bin = PROJECT_ROOT / "api" / api_bin_name
    if api_bin.exists():
        print(f"{C.GREEN}✓ Go 二进制 api/{api_bin_name} [已编译]{C.RESET}")
    else:
        print(f"{C.YELLOW}✗ Go 二进制 api/{api_bin_name} [未编译]{C.RESET}")
        print(f"{C.CYAN}  → 运行: cd api && go build -o {api_bin_name} .{C.RESET}")
        ok = False

    nm = PROJECT_ROOT / "web" / "node_modules"
    if nm.exists():
        print(f"{C.GREEN}✓ web/node_modules [已安装]{C.RESET}")
    else:
        print(f"{C.YELLOW}✗ web/node_modules [未安装]{C.RESET}")
        print(f"{C.CYAN}  → 运行: cd web && npm install{C.RESET}")
        ok = False

    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        print(f"{C.GREEN}✓ .env [已存在]{C.RESET}")
    else:
        print(f"{C.CYAN}○ .env [不存在，安装时会自动创建]{C.RESET}")

    output_dir = PROJECT_ROOT / "output"
    if output_dir.exists():
        print(f"{C.GREEN}✓ output/ [已存在]{C.RESET}")
    else:
        print(f"{C.CYAN}○ output/ [不存在，安装时会自动创建]{C.RESET}")
    return ok


def cmd_check():
    """--check 子命令：仅做环境检测，不安装。返回退出码（0=满足 / 1=有缺失）。"""
    print_check_header()
    print(f"{C.BOLD}━━━ 运行时检测 ━━━{C.RESET}")
    rt_ok = check_python(strict=False)
    check_os()
    node_ok, _ = check_node()
    rt_ok = rt_ok and node_ok
    go_ok, _ = check_go()
    rt_ok = rt_ok and go_ok
    ffmpeg_ok, ffmpeg_ver = check_ffmpeg()
    if ffmpeg_ok:
        print(f"{C.GREEN}✓ FFmpeg {ffmpeg_ver}（可选）{C.RESET}")
    else:
        print(f"{C.YELLOW}✗ FFmpeg 未安装（可选，仅视频导出需要）{C.RESET}")
        print(f"{C.CYAN}  → 下载: https://ffmpeg.org/download.html{C.RESET}")

    print(f"\n{C.BOLD}━━━ Python 依赖 ━━━{C.RESET}")
    deps_ok = check_python_deps_report()

    print(f"\n{C.BOLD}━━━ 构件检测 ━━━{C.RESET}")
    art_ok = check_artifacts_report()

    all_ok = rt_ok and deps_ok and art_ok
    print()
    if all_ok:
        print(f"{C.GREEN}{C.BOLD}✅ 全部满足，可直接运行：python start.py{C.RESET}")
        return 0
    print(f"{C.YELLOW}{C.BOLD}⚠ 有缺失项，运行 python install.py 进行修复{C.RESET}")
    return 1


def print_summary(state):
    """打印安装总结（成功 / 失败 / 跳过步骤列表）+ 下一步提示。"""
    print(f"\n{C.BOLD}━━━ 安装总结 ━━━{C.RESET}")
    if state.success:
        print(f"{C.GREEN}✓ 成功步骤 ({len(state.success)}):{C.RESET}")
        for name in state.success:
            print(f"  {C.GREEN}• {name}{C.RESET}")
    if state.failed:
        print(f"{C.RED}✗ 失败步骤 ({len(state.failed)}):{C.RESET}")
        for name in state.failed:
            print(f"  {C.RED}• {name}{C.RESET}")
    if state.skipped:
        print(f"{C.YELLOW}○ 跳过步骤 ({len(state.skipped)}):{C.RESET}")
        for name in state.skipped:
            print(f"  {C.YELLOW}• {name}{C.RESET}")
    if not (state.success or state.failed or state.skipped):
        print(f"{C.YELLOW}○ 无步骤执行{C.RESET}")

    print()
    if state.failed:
        print(f"{C.YELLOW}💡 下一步：修复上述失败项后重新运行 python install.py{C.RESET}")
        if FAQ_PATH.exists():
            print(f"{C.YELLOW}   修复指引：{FAQ_PATH}{C.RESET}")
    else:
        print(f"{C.CYAN}💡 下一步：{C.RESET}")
        print(f"   现在你可以运行: {C.GREEN}python start.py{C.RESET}")
        if (PROJECT_ROOT / "start.sh").exists():
            print(f"   或者:           {C.GREEN}bash start.sh{C.RESET}")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Live2D Master Agent Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python install.py              # 完整安装\n"
            "  python install.py --minimal    # 仅核心\n"
            "  python install.py --check      # 仅检测环境，不安装\n"
            "  python install.py --dry-run    # 打印将执行的命令但不执行\n"
            "  python install.py --yes        # 非交互（失败即中止）"
        ),
    )
    parser.add_argument("--minimal", action="store_true", help="Core only")
    parser.add_argument("--ai", action="store_true", help="Include AI models (large)")
    parser.add_argument("--dev", action="store_true", help="Dev tools + test deps")
    parser.add_argument("--skip-web", action="store_true", help="Skip web frontend")
    parser.add_argument("--skip-api", action="store_true", help="Skip Go API")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive")
    parser.add_argument("--check", action="store_true",
                        help="仅检测环境，不安装（退出码 0=满足 / 1=有缺失）")
    parser.add_argument("--dry-run", action="store_true",
                        help="打印将执行的命令但不实际执行")
    args = parser.parse_args()

    if args.check:
        sys.exit(cmd_check())

    state = InstallState(dry_run=args.dry_run, yes=args.yes)

    if args.dry_run:
        print(f"{C.CYAN}{C.BOLD}🔍 dry-run 模式：仅打印将执行的命令，不实际安装{C.RESET}\n")

    print_header()

    check_python()
    check_os()
    has_node, _ = check_node()
    has_go, _ = check_go()

    print()

    if state.dry_run:
        print(f"{C.CYAN}[dry-run] 创建目录: assets/characters, assets/output, assets/models, output, logs{C.RESET}")
    else:
        create_directories()

    install_core(state)
    if state.abort:
        print_summary(state)
        sys.exit(2)

    if not args.minimal:
        install_desktop_pet(state)
        if state.abort:
            print_summary(state)
            sys.exit(2)
    else:
        print(f"\n{C.YELLOW}Skipping desktop pet (--minimal){C.RESET}")
        state.mark_skipped("Desktop Pet Dependencies")

    if args.ai:
        install_ai_models(state)
        if state.abort:
            print_summary(state)
            sys.exit(2)

    if not args.skip_web:
        install_web(has_node, state)
        if state.abort:
            print_summary(state)
            sys.exit(2)
    else:
        state.mark_skipped("Web Frontend")

    if not args.skip_api:
        install_api(has_go, state)
        if state.abort:
            print_summary(state)
            sys.exit(2)
    else:
        state.mark_skipped("Go API Server")

    if args.dev:
        if run_pip(["pytest>=7.0", "pytest-asyncio>=0.21", "pytest-cov>=4.0"],
                   "Installing dev tools...", state):
            state.mark_success("Dev Tools")

    if state.dry_run:
        print(f"{C.CYAN}[dry-run] 创建 .env（如不存在）{C.RESET}")
    else:
        create_env_file()

    success = verify_installation() if not state.dry_run else True

    print_next_steps(has_node and not args.skip_web, has_go and not args.skip_api)

    print_summary(state)

    if state.failed:
        sys.exit(2)
    if success:
        print(f"{C.GREEN}{C.BOLD}🎉 You're all set!{C.RESET}")
        sys.exit(0)
    else:
        print(f"{C.YELLOW}Some optional components may be missing, but core should work.{C.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
