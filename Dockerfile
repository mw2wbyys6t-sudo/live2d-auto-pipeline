# Live2D Master Agent v10.0 - Multi-stage Dockerfile
# Provides: Python core + Go API + Next.js web UI

# ===== Stage 1: Build Go API =====
FROM golang:1.22-alpine AS go-builder
WORKDIR /build
COPY api/ .
RUN go mod tidy && CGO_ENABLED=0 go build -o /live2d-api .

# ===== Stage 2: Build Next.js Web =====
FROM node:20-alpine AS web-builder
WORKDIR /app
COPY web/package*.json ./
RUN npm ci
COPY web/ .
RUN npm run build

# ===== Stage 3: Final Runtime =====
FROM python:3.11-slim-bookworm

LABEL maintainer="Live2D Master Agent"
LABEL description="AI Character → Live2D Model → Desktop Pet"
LABEL version="10.0"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender1 \
    libgomp1 ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY core/ ./core/
COPY drivers/ ./drivers/
COPY llm_bridge/ ./llm_bridge/
COPY live2d_builder/ ./live2d_builder/
COPY scripts/ ./scripts/
COPY prompts/ ./prompts/
COPY templates/ ./templates/
COPY .env.example ./.env.example

# Copy Go binary
COPY --from=go-builder /live2d-api ./api/live2d-api

# Copy built web
COPY --from=web-builder /app/.next ./web/.next
COPY --from=web-builder /app/public ./web/public
COPY --from=web-builder /app/package.json ./web/package.json
COPY --from=web-builder /app/node_modules ./web/node_modules
COPY web/next.config.js ./web/

# Create directories
RUN mkdir -p assets/characters assets/output assets/models output logs

# Environment
ENV PYTHONPATH=/app
ENV LIVE2D_PROJECT_ROOT=/app
ENV GO_API_HOST=0.0.0.0
ENV GO_API_PORT=8080
ENV NEXT_PUBLIC_API_URL=http://localhost:8080

EXPOSE 8080 3000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Copy and set entrypoint
COPY deploy/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
