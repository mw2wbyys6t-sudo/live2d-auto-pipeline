#!/bin/bash
set -e

echo "🎭 Live2D Master Agent v10.0"
echo "============================"

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
npx next start -p 3000 &
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
wait
