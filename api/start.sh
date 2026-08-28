#!/bin/bash
# Live2D Master Agent API 启动脚本

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     🎨 Live2D Master Agent API 启动器                       ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo

# 检查 Go 环境
echo -e "${YELLOW}[1/5] 检查 Go 环境...${NC}"
if ! command -v go &> /dev/null; then
    echo -e "${RED}❌ Go 未安装，请先安装 Go${NC}"
    exit 1
fi
GO_VERSION=$(go version | awk '{print $3}')
echo -e "${GREEN}✅ Go 版本: $GO_VERSION${NC}"

# 设置国内代理（如果需要）
if [ -z "$GOPROXY" ]; then
    export GOPROXY=https://goproxy.cn,direct
    echo -e "${YELLOW}   已设置 GOPROXY=$GOPROXY${NC}"
fi

# 下载依赖
echo -e "${YELLOW}[2/5] 下载依赖...${NC}"
go mod tidy
echo -e "${GREEN}✅ 依赖下载完成${NC}"

# 编译
echo -e "${YELLOW}[3/5] 编译...${NC}"
go build -o live2d-api .
echo -e "${GREEN}✅ 编译完成${NC}"

# 检查可执行文件
if [ ! -f "$SCRIPT_DIR/live2d-api" ]; then
    echo -e "${RED}❌ 编译失败，未找到可执行文件${NC}"
    exit 1
fi

# 确保输出目录存在
mkdir -p "$SCRIPT_DIR/../output"

# 启动服务
echo -e "${YELLOW}[4/5] 启动服务...${NC}"
echo

# 解析命令行参数
HOST=""
PORT=""
CONFIG=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="-host $2"
            shift 2
            ;;
        --port)
            PORT="-port $2"
            shift 2
            ;;
        --config)
            CONFIG="-config $2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo -e "${GREEN}✅ 启动 Live2D API 服务...${NC}"
echo

# 启动服务
exec ./live2d-api $HOST $PORT $CONFIG
