#!/usr/bin/env bash
# Live2D Master Agent - 一键运行所有测试
# 使用: ./run_all_tests.sh [--no-services] [--skip-e2e]
#   --no-services  跳过启停服务（假设已启动）
#   --skip-e2e     跳过端到端测试
#   --skip-go      跳过 Go 测试
#   --skip-py      跳过 Python 测试

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;34m'
NC='\033[0m'

# 解析参数
NO_SERVICES=false
SKIP_E2E=false
SKIP_GO=false
SKIP_PY=false
for arg in "$@"; do
  case $arg in
    --no-services) NO_SERVICES=true ;;
    --skip-e2e)    SKIP_E2E=true ;;
    --skip-go)     SKIP_GO=true ;;
    --skip-py)     SKIP_PY=true ;;
  esac
done

RESULTS=()

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}     🧪 Live2D Master Agent - 自动化测试套件${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# 启动服务（如需要）
STARTED_GO_PID=""
STARTED_WEB_PID=""
if [ "$NO_SERVICES" = false ] && [ "$SKIP_E2E" = false ]; then
  echo -e "${YELLOW}[1/5] 启动 Go API 服务...${NC}"
  if ! curl -s -f -m 2 http://localhost:8080/api/health >/dev/null 2>&1; then
    cd api
    GOMEMLIMIT=4096MiB go build -o /tmp/live2d-api . 2>&1 | tail -5
    /tmp/live2d-api -port 8080 -host 0.0.0.0 >/tmp/live2d-api.log 2>&1 &
    STARTED_GO_PID=$!
    cd ..
    sleep 2
    if curl -s -f -m 3 http://localhost:8080/api/health >/dev/null 2>&1; then
      echo -e "${GREEN}   ✅ Go API 已启动 (PID: $STARTED_GO_PID)${NC}"
    else
      echo -e "${RED}   ❌ Go API 启动失败${NC}"
      cat /tmp/live2d-api.log | tail -20
      exit 1
    fi
  else
    echo -e "${GREEN}   ✅ Go API 已在运行${NC}"
  fi

  echo -e "${YELLOW}[2/5] 启动 Next.js 前端...${NC}"
  if ! curl -s -f -m 2 http://localhost:3000/ >/dev/null 2>&1; then
    cd web
    if [ ! -d node_modules ]; then
      npm install --no-audit --no-fund --prefer-offline >/dev/null 2>&1
    fi
    # Use a dedicated dev cache (separate from the production .next) so the
    # two never share generated types on slow/networked filesystems.
    export NEXT_DIST_DIR=".next-dev"
    rm -rf "$NEXT_DIST_DIR" 2>/dev/null || true
    NEXT_PUBLIC_API_URL=http://localhost:8080 npm run dev -- -p 3000 -H 0.0.0.0 \
      >/tmp/nextjs.log 2>&1 &
    STARTED_WEB_PID=$!
    cd ..
    # Cold `next dev` (Turbopack) can take 30-60s to be ready; wait up to 90s.
    for i in $(seq 1 45); do
      if curl -s -f -m 2 http://localhost:3000/ >/dev/null 2>&1; then
        break
      fi
      sleep 2
    done
    if curl -s -f -m 5 http://localhost:3000/ >/dev/null 2>&1; then
      echo -e "${GREEN}   ✅ Next.js 已启动 (PID: $STARTED_WEB_PID)${NC}"
    else
      echo -e "${RED}   ❌ Next.js 启动失败${NC}"
      tail -20 /tmp/nextjs.log
      exit 1
    fi
    # Warm-compile the key routes so on-demand compilation doesn't exceed
    # the e2e per-request timeout.
    for p in / /live2d /generate /characters /chat /export; do
      curl -s -f -m 60 "http://localhost:3000$p" >/dev/null 2>&1 || true
    done
  else
    echo -e "${GREEN}   ✅ Next.js 已在运行${NC}"
  fi
fi

cleanup() {
  if [ -n "$STARTED_GO_PID" ]; then
    kill -9 $STARTED_GO_PID 2>/dev/null || true
    echo -e "${YELLOW}   已停止 Go API${NC}"
  fi
  if [ -n "$STARTED_WEB_PID" ]; then
    kill -9 $STARTED_WEB_PID 2>/dev/null || true
    echo -e "${YELLOW}   已停止 Next.js${NC}"
  fi
}
trap cleanup EXIT

# 1. Go 单元测试
if [ "$SKIP_GO" = false ]; then
  echo ""
  echo -e "${BLUE}[3/5] Go 单元测试 (services + handlers)${NC}"
  cd api
  if GOMEMLIMIT=4096MiB go test -vet=off ./services/ ./handlers/ 2>&1 | tail -20; then
    RESULTS+=("Go单元: ✅")
  else
    RESULTS+=("Go单元: ❌")
  fi
  cd ..
fi

# 2. Python 单元测试
if [ "$SKIP_PY" = false ]; then
  echo ""
  echo -e "${BLUE}[4/5] Python 单元测试 (unit/)${NC}"
  if timeout 180 python -m pytest tests/unit/ -q --no-header 2>&1 | tail -15; then
    RESULTS+=("Py单元: ✅")
  else
    RESULTS+=("Py单元: ❌")
  fi

  echo ""
  echo -e "${BLUE}[4.5/5] Python 接口契约测试 (integration/)${NC}"
  if timeout 240 python -m pytest tests/integration/ -q --no-header 2>&1 | tail -10; then
    RESULTS+=("接口契约: ✅")
  else
    RESULTS+=("接口契约: ❌")
  fi
fi

# 3. E2E 测试
if [ "$SKIP_E2E" = false ]; then
  echo ""
  echo -e "${BLUE}[5/5] 端到端测试 (e2e/)${NC}"
  if timeout 120 python -m pytest tests/e2e/ -v --no-header 2>&1 | tail -25; then
    RESULTS+=("E2E: ✅")
  else
    RESULTS+=("E2E: ❌")
  fi
fi

# 总结
echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}     📊 测试结果汇总${NC}"
echo -e "${BLUE}============================================================${NC}"
for r in "${RESULTS[@]}"; do
  echo -e "  $r"
done
echo ""

# 判断是否有失败
if printf '%s\n' "${RESULTS[@]}" | grep -q "❌"; then
  echo -e "${RED}❌ 有测试失败${NC}"
  exit 1
else
  echo -e "${GREEN}🎉 所有测试通过！${NC}"
  exit 0
fi
