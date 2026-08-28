#!/usr/bin/env bash
# Live2D Master Agent v10.0 - Linux/macOS Installer
set -e

echo "🎭 Live2D Master Agent v10.0 Installer"
echo "========================================"

# Check Python
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo "❌ Python 3.9+ is required. Please install it first."
    exit 1
fi

echo "✓ Using $PY ($($PY --version))"

# Create virtual environment (optional but recommended)
if [ ! -d ".venv" ]; then
    read -p "Create virtual environment? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        $PY -m venv .venv
        source .venv/bin/activate
        echo "✓ Virtual environment created and activated"
    fi
fi

# Run Python installer
$PY install.py "$@"

echo ""
echo "🎉 Installation complete!"
echo ""
echo "If using venv, activate it first:"
echo "  source .venv/bin/activate"
echo ""
echo "Quick start:"
echo "  python -m core.workflow '蓝发猫耳少女' --deploy-desktop"
