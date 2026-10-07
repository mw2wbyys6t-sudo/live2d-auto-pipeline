#!/usr/bin/env bash
# Live2D Master Agent 一键启动器（Linux/macOS）
# 这是一个薄包装：真正的逻辑在 start.py 里。
cd "$(dirname "$0")" || exit 1
if command -v python3 &>/dev/null; then PY=python3
elif command -v python &>/dev/null; then PY=python
else echo "❌ Python 未安装，请先安装 Python 3.9+：https://www.python.org/downloads/"; exit 1; fi
exec "$PY" start.py "$@"
