#!/usr/bin/env bash
# ======================================================================
# Live2D Master Agent - 桌面版一键构建（Linux/macOS 版）
#
# 与 scripts/build_desktop.bat 完全对等。允许在非 Windows 主机上交叉
# 编译 Windows 桌面 exe，便于 CI 和 macOS/Linux 开发者参与桌面打包。
#
# 产物：
#   dist/Live2DMasterAgent.exe         桌面版（无控制台，双击运行）
#   dist/Live2DMasterAgent-console.exe 调试版（带控制台日志）
#
# 前置：
#   - Node.js + npm（用于 web/ 构建）
#   - Go ≥ 1.21（自带 Windows 交叉编译，无需额外工具链）
#   - Bash 4+ 或 Zsh
#
# 用法：
#   ./scripts/build_desktop.sh              # 默认目标：windows
#   ./scripts/build_desktop.sh --target linux   # 也可构建本机调试版
#   ./scripts/build_desktop.sh --skip-frontend  # 跳过前端构建（用现有 web/out）
# ======================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

TARGET="windows"
SKIP_FRONTEND=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --skip-frontend) SKIP_FRONTEND=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
done

# 根据 target 选择 GOOS / GOARCH / 输出后缀 / ldflags
case "$TARGET" in
  windows)
    export GOOS=windows GOARCH=amd64
    EXE_NAME="Live2DMasterAgent.exe"
    GUI_LDFLAGS="-s -w -H windowsgui"
    DBG_LDFLAGS=""
    ;;
  linux)
    export GOOS=linux GOARCH=amd64
    EXE_NAME="Live2DMasterAgent"
    GUI_LDFLAGS="-s -w"
    DBG_LDFLAGS=""
    ;;
  darwin)
    export GOOS=darwin GOARCH=arm64
    EXE_NAME="Live2DMasterAgent"
    GUI_LDFLAGS="-s -w"
    DBG_LDFLAGS=""
    ;;
  *) echo "❌ 不支持的 target: $TARGET（可选: windows / linux / darwin）"; exit 2 ;;
esac

echo "[1/5] 目标平台：$TARGET → $EXE_NAME"

if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  echo "[2/5] 构建前端静态站点（NEXT_STATIC_EXPORT=1）..."
  pushd web >/dev/null
  NEXT_STATIC_EXPORT=1 npm run build
  popd >/dev/null
else
  echo "[2/5] 跳过前端构建（使用现有 web/out）"
fi

if [[ ! -d web/out ]]; then
  echo "❌ web/out 不存在；请去掉 --skip-frontend 重新运行"
  exit 1
fi

echo "[3/5] 版本化静态资源 URL（防升级后浏览器缓存错版）..."
node scripts/version-assets.mjs web/out

echo "[4/5] 复制静态产物到内嵌目录 api/webui/dist ..."
rm -rf api/webui/dist
mkdir -p api/webui/dist
cp -r web/out/* api/webui/dist/
# 保留 .gitkeep 让 go:embed 在纯 API 构建时不报错
touch api/webui/dist/.gitkeep

echo "[5/5] 编译桌面程序（目标 $GOOS/$GOARCH）..."
mkdir -p dist
# 如有旧进程占用，先尝试停止（仅本机目标时生效）
if [[ "$GOOS" == "$(go env GOOS)" ]]; then
  pkill -f "$EXE_NAME" 2>/dev/null || true
fi

pushd api >/dev/null
go build -trimpath -ldflags "$GUI_LDFLAGS" -o "../dist/$EXE_NAME" .
if [[ "$TARGET" == "windows" ]]; then
  # 调试版：带控制台，便于查看 Python 桥接日志
  go build -trimpath -ldflags "$DBG_LDFLAGS" -o "../dist/${EXE_NAME%.exe}-console.exe" .
fi
popd >/dev/null

echo ""
echo "✅ 完成。"
ls -lh "dist/$EXE_NAME" 2>/dev/null | awk '{print "  桌面版:", $9, $5}'
if [[ "$TARGET" == "windows" ]]; then
  ls -lh "dist/${EXE_NAME%.exe}-console.exe" 2>/dev/null | awk '{print "  调试版:", $9, $5}'
fi
echo ""
echo "提示：把 $EXE_NAME 放在项目根目录可直接发现 Python 工程；"
echo "      否则用 $EXE_NAME -config 你的配置.json 指定 scripts_dir。"
