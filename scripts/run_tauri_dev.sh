#!/usr/bin/env bash
set -euo pipefail

# Live2D Master Agent - Tauri dev launcher (Linux/macOS)
cd "$(dirname "$0")/.."

echo "[1/3] Checking tauri-cli..."
if ! cargo tauri --version >/dev/null 2>&1; then
    echo "[!] tauri-cli not found, installing..."
    cargo install tauri-cli --version "^2"
fi

echo "[2/3] Building Go API binary..."
(
    cd api
    go build -o live2d-api .
)

echo "[3/3] Starting Tauri dev window..."
echo ""
echo "  Tauri window will open, loading http://localhost:8080"
echo "  Close window = automatically stop Go backend"
echo ""
cd desktop
cargo tauri dev
