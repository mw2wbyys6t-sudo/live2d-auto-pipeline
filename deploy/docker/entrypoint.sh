#!/bin/bash
set -e

echo "🎭 Live2D Master Agent v0.10.0"
echo "============================"

# 以 root 启动时：先把 bind mount 的可写目录属主改成目标用户，再通过 gosu 降权
# 重入本脚本。此后 Go API 与 Next.js 全部以非 root 身份运行。
# 以非 root 身份启动时（如 compose 覆盖了 user:）直接跳过，避免误 chown。
if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"
    mkdir -p /app/assets /app/output /app/logs
    chown -R "${PUID}:${PGID}" /app/assets /app/output /app/logs 2>/dev/null || true
    exec gosu "${PUID}:${PGID}" "$0" "$@"
fi

# Create .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from template"
fi

# Start Go API server in background
echo "Starting API server on port ${GO_API_PORT:-8080}..."
cd /app/api
./live2d-api &
API_PID=$!
cd /app

# Wait for API to be ready
echo "Waiting for API to start..."
for i in $(seq 1 30); do
    if curl -s http://localhost:${GO_API_PORT:-8080}/api/health > /dev/null 2>&1; then
        echo "API is ready!"
        break
    fi
    sleep 1
done

# Start Next.js web server
echo "Starting web UI on port 3000..."
cd /app/web
node node_modules/next/dist/bin/next start -p 3000 &
WEB_PID=$!
cd /app

echo ""
echo "✅ All services started!"
echo "   API:    http://localhost:${GO_API_PORT:-8080}"
echo "   Web UI: http://localhost:3000"
echo ""

# Trap shutdown
trap "kill $API_PID $WEB_PID 2>/dev/null; exit 0" SIGTERM SIGINT

# Wait
set +e
wait -n "$API_PID" "$WEB_PID"
STATUS=$?
kill "$API_PID" "$WEB_PID" 2>/dev/null
wait
exit "$STATUS"
