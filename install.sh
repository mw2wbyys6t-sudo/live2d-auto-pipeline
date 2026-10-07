#!/usr/bin/env bash
# Live2D Master Agent - Linux/macOS Installer
# 透传所有参数给 install.py（支持 --check / --dry-run / --minimal / --yes 等）
set -e

echo "🎭 Live2D Master Agent Installer"
echo "========================================"

# 预检 1：Python 必须存在，否则不进入 install.py（避免用户看到 Python traceback）
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo ""
    echo "❌ 未检测到 Python。Live2D Master Agent 需要 Python 3.9+。"
    echo ""
    echo "请先安装 Python："
    echo "  • 通用下载页: https://www.python.org/downloads/"
    echo "  • macOS:      brew install python@3.12"
    echo "  • Ubuntu:     sudo apt-get install python3 python3-venv python3-pip"
    echo "  • Fedora:     sudo dnf install python3"
    echo "  • Arch:       sudo pacman -S python"
    echo ""
    exit 1
fi

# 预检 2：Python 版本 ≥ 3.9（交给 python 自己判断，比 bash 解析版本号更可靠）
if ! "$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>/dev/null; then
    PY_VER=$("$PY" --version 2>&1)
    echo ""
    echo "❌ Python 版本过低：$PY_VER（需要 3.9+）"
    echo "   下载新版本：https://www.python.org/downloads/"
    echo ""
    exit 1
fi

echo "✓ Using $PY ($("$PY" --version 2>&1))"

# 可选：创建虚拟环境（venv）。非交互环境下跳过询问，避免 read 卡死或 set -e 退出。
if [ ! -d ".venv" ]; then
    REPLY=""
    if [ -t 0 ]; then
        read -p "Create virtual environment? [Y/n] " -n 1 -r || true
        echo
    else
        echo "• 非交互环境，跳过 venv 询问（将创建虚拟环境）"
    fi
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        "$PY" -m venv .venv
        # shellcheck disable=SC1091
        source .venv/bin/activate
        echo "✓ Virtual environment created and activated"
        PY=python
    fi
fi

# 运行 Python 安装器，透传所有参数；关闭 set -e 以便捕获并反馈退出码
set +e
"$PY" install.py "$@"
INSTALL_RC=$?
set -e

echo ""
if [ "$INSTALL_RC" -eq 0 ]; then
    echo "🎉 Installation complete!"
elif [ "$INSTALL_RC" -eq 1 ]; then
    echo "⚠ 环境检测发现缺失项（退出码 1）。运行 install.py --check 查看详情。"
elif [ "$INSTALL_RC" -eq 2 ]; then
    echo "⚠ 部分安装步骤失败（退出码 2）。请查看上方输出与 docs/FAQ.md。"
else
    echo "⚠ 安装未正常完成（退出码 $INSTALL_RC）。"
fi
echo ""
if [ -d ".venv" ]; then
    echo "If using venv, activate it first:"
    echo "  source .venv/bin/activate"
    echo ""
fi
echo "Quick start:"
echo "  python -m core.workflow '蓝发猫耳少女' --deploy-desktop"
echo "  python start.py                # 启动 Go API + Next.js"
echo "  python install.py --check      # 仅检测环境"
echo "  python install.py --dry-run    # 预览将执行的命令"

exit "$INSTALL_RC"
