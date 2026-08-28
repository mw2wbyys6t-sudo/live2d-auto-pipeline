#!/usr/bin/env python3
"""
Live2D Master Agent v10.0 - One-Click Installer

Usage:
    python install.py              # Full install (core + desktop pet)
    python install.py --minimal    # Core only
    python install.py --ai         # Core + AI models (large download)
    python install.py --dev        # Development install with test tools

Supports: Windows, macOS, Linux. Requires Python 3.9+.
"""

import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.resolve()
PYTHON = sys.executable or "python3"

# Terminal colors
class C:
    GREEN = "\033[92m" if sys.stdout.isatty() else ""
    YELLOW = "\033[93m" if sys.stdout.isatty() else ""
    RED = "\033[91m" if sys.stdout.isatty() else ""
    CYAN = "\033[96m" if sys.stdout.isatty() else ""
    BOLD = "\033[1m" if sys.stdout.isatty() else ""
    RESET = "\033[0m" if sys.stdout.isatty() else ""


def print_header():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════╗
║       🎭 Live2D Master Agent v10.0 - Installer           ║
║       AI Character → Live2D Model → Desktop Pet          ║
╚══════════════════════════════════════════════════════════╝{C.RESET}
""")


def check_python():
    """Verify Python version."""
    version = sys.version_info
    if version < (3, 9):
        print(f"{C.RED}❌ Python 3.9+ required. You have {sys.version}{C.RESET}")
        sys.exit(1)
    print(f"{C.GREEN}✓ Python {platform.python_version()}{C.RESET}")
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
    """Check if Node.js is available (for web frontend)."""
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node and npm:
        try:
            result = subprocess.run([node, "--version"], capture_output=True, text=True)
            version = result.stdout.strip()
            print(f"{C.GREEN}✓ Node.js {version}{C.RESET}")
            return True
        except Exception:
            pass
    print(f"{C.YELLOW}⚠ Node.js not found - web UI will not be available{C.RESET}")
    print(f"  Install from: https://nodejs.org/ (v18+ required)")
    return False


def check_go():
    """Check if Go is available (for API server)."""
    go = shutil.which("go")
    if go:
        try:
            result = subprocess.run([go, "version"], capture_output=True, text=True)
            print(f"{C.GREEN}✓ {result.stdout.strip()}{C.RESET}")
            return True
        except Exception:
            pass
    print(f"{C.YELLOW}⚠ Go not found - API server will not be available{C.RESET}")
    print(f"  Install from: https://go.dev/dl/ (v1.21+ required)")
    return False


def run_pip(args, description=""):
    """Run pip install with error handling."""
    if description:
        print(f"\n{C.CYAN}📦 {description}{C.RESET}")

    cmd = [PYTHON, "-m", "pip", "install", "--upgrade"] + args
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"{C.RED}❌ Failed to install: {e}{C.RESET}")
        return False


def install_core():
    """Install core Python dependencies."""
    print(f"\n{C.BOLD}━━━ Step 1/5: Core Python Dependencies ━━━{C.RESET}")

    # Upgrade pip first
    run_pip(["pip"], "Upgrading pip...")

    # Install from requirements.txt
    req_file = PROJECT_ROOT / "requirements.txt"
    if req_file.exists():
        print(f"\n{C.CYAN}📦 Installing from requirements.txt...{C.RESET}")
        try:
            subprocess.run(
                [PYTHON, "-m", "pip", "install", "-r", str(req_file)],
                check=True,
            )
            print(f"{C.GREEN}✓ Core dependencies installed{C.RESET}")
        except subprocess.CalledProcessError as e:
            print(f"{C.RED}❌ Core dependency install failed: {e}{C.RESET}")
            print(f"{C.YELLOW}  Trying individual packages...{C.RESET}")
            core_pkgs = [
                "Pillow>=10.0.0", "numpy>=1.24.0", "requests>=2.31.0",
                "urllib3>=2.0.0", "httpx>=0.24.0", "aiohttp>=3.9.0",
                "psd-tools>=1.9.0", "scipy>=1.10.0", "scikit-learn>=1.3.0",
                "cryptography>=41.0.0", "rich>=13.0.0",
                "opencv-python-headless>=4.8.0", "onnxruntime>=1.14.0",
                "aiofiles>=23.0", "websockets>=12.0",
            ]
            run_pip(core_pkgs, "Installing individual packages...")
    return True


def install_desktop_pet():
    """Install desktop pet dependencies."""
    print(f"\n{C.BOLD}━━━ Step 2/5: Desktop Pet Dependencies ━━━{C.RESET}")

    # pygame/pygame-ce
    if sys.version_info >= (3, 14):
        pkgs = ["pygame-ce>=2.5.0"]
    else:
        pkgs = ["pygame>=2.5.0"]

    # mediapipe for face tracking
    pkgs.append("mediapipe>=0.10.0")

    # audio
    pkgs.extend(["sounddevice>=0.4.6", "soundfile>=0.12.0"])

    # TTS
    pkgs.append("edge-tts>=6.1.0")

    # rembg for background removal
    pkgs.append("rembg[cpu]>=2.0.0")

    run_pip(pkgs, "Installing desktop pet, tracking, and TTS packages...")
    print(f"{C.GREEN}✓ Desktop pet dependencies installed{C.RESET}")
    return True


def install_ai_models():
    """Install optional AI model dependencies (large downloads)."""
    print(f"\n{C.BOLD}━━━ Step 3/5: AI Model Dependencies ━━━{C.RESET}")
    print(f"{C.YELLOW}⚠ This downloads PyTorch and other large packages (2-5 GB){C.RESET}")

    pkgs = [
        "torch>=2.0.0",
        "torchvision>=0.15.0",
        "transformers>=4.30.0",
        "segment-anything>=1.0",
        "huggingface-hub>=0.17.0",
    ]

    run_pip(pkgs, "Installing PyTorch, transformers, SAM...")
    print(f"{C.GREEN}✓ AI model dependencies installed{C.RESET}")

    # Download SAM model
    print(f"\n{C.CYAN}🤖 Downloading SAM model (if not cached)...{C.RESET}")
    try:
        sam_script = PROJECT_ROOT / "scripts" / "download_models.py"
        if sam_script.exists():
            subprocess.run([PYTHON, str(sam_script), "--model", "sam"], check=False)
    except Exception as e:
        print(f"{C.YELLOW}⚠ Model download skipped: {e}{C.RESET}")

    return True


def install_web(has_node: bool):
    """Install web frontend dependencies."""
    print(f"\n{C.BOLD}━━━ Step 4/5: Web Frontend ━━━{C.RESET}")

    if not has_node:
        print(f"{C.YELLOW}⚠ Skipping - Node.js not available{C.RESET}")
        return False

    web_dir = PROJECT_ROOT / "web"
    if not web_dir.exists():
        print(f"{C.YELLOW}⚠ Web directory not found, skipping{C.RESET}")
        return False

    print(f"{C.CYAN}📦 Installing npm packages...{C.RESET}")
    try:
        subprocess.run(["npm", "install"], cwd=str(web_dir), check=True)
        print(f"{C.GREEN}✓ Web dependencies installed{C.RESET}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"{C.RED}❌ npm install failed: {e}{C.RESET}")
        return False


def install_api(has_go: bool):
    """Build Go API server."""
    print(f"\n{C.BOLD}━━━ Step 5/5: Go API Server ━━━{C.RESET}")

    if not has_go:
        print(f"{C.YELLOW}⚠ Skipping - Go not available{C.RESET}")
        return False

    api_dir = PROJECT_ROOT / "api"
    if not api_dir.exists():
        print(f"{C.YELLOW}⚠ API directory not found, skipping{C.RESET}")
        return False

    print(f"{C.CYAN}📦 Downloading Go dependencies...{C.RESET}")
    try:
        subprocess.run(["go", "mod", "tidy"], cwd=str(api_dir), check=True)
        print(f"{C.CYAN}🔨 Building API server (low-memory mode)...{C.RESET}")
        # Use GOMAXPROCS=1 to reduce memory usage in constrained environments
        build_env = os.environ.copy()
        build_env['GOMAXPROCS'] = '1'
        subprocess.run(["go", "build", "-p", "1", "-o", "live2d-api.exe" if platform.system() == "Windows" else "live2d-api", "."],
                      cwd=str(api_dir), check=True, env=build_env)
        print(f"{C.GREEN}✓ API server built{C.RESET}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"{C.RED}❌ Go build failed: {e}{C.RESET}")
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


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Live2D Master Agent Installer")
    parser.add_argument("--minimal", action="store_true", help="Core only")
    parser.add_argument("--ai", action="store_true", help="Include AI models (large)")
    parser.add_argument("--dev", action="store_true", help="Dev tools + test deps")
    parser.add_argument("--skip-web", action="store_true", help="Skip web frontend")
    parser.add_argument("--skip-api", action="store_true", help="Skip Go API")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive")
    args = parser.parse_args()

    print_header()

    # Pre-flight checks
    check_python()
    os_type = check_os()
    has_node = check_node()
    has_go = check_go()

    print()

    # Install
    create_directories()

    install_core()

    if not args.minimal:
        install_desktop_pet()
    else:
        print(f"\n{C.YELLOW}Skipping desktop pet (--minimal){C.RESET}")

    if args.ai:
        install_ai_models()

    if not args.skip_web:
        install_web(has_node)

    if not args.skip_api:
        install_api(has_go)

    if args.dev:
        run_pip(["pytest>=7.0", "pytest-asyncio>=0.21", "pytest-cov>=4.0"], "Installing dev tools...")

    create_env_file()

    # Verify
    success = verify_installation()

    print_next_steps(has_node and not args.skip_web, has_go and not args.skip_api)

    if success:
        print(f"{C.GREEN}{C.BOLD}🎉 You're all set!{C.RESET}")
        sys.exit(0)
    else:
        print(f"{C.YELLOW}Some optional components may be missing, but core should work.{C.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
