#!/usr/bin/env bash
set -euo pipefail

# Live2D Master Agent - Tauri dev launcher (Linux/macOS)
# Full build chain: frontend -> version assets -> embed -> Go -> Tauri
cd "$(dirname "$0")/.."

echo "=========================================="
echo " Live2D Master Agent - Tauri Desktop"
echo "=========================================="
echo

# ---- Prerequisites ----
echo "[0/5] Checking prerequisites..."

if ! command -v cargo &>/dev/null; then
    echo "[X] cargo not found, install Rust first"; exit 1
fi

if ! cargo tauri --version >/dev/null 2>&1; then
    echo "[!] tauri-cli not found, installing..."
    cargo install tauri-cli --version "^2"
fi

if [ ! -d "web/node_modules" ]; then
    echo "[!] web/node_modules missing, installing frontend deps..."
    (cd web && npm install)
fi

echo "[OK] Prerequisites satisfied"
echo

# ---- Step 1: Build frontend ----
echo "[1/5] Building frontend static export..."
(
    cd web
    NEXT_STATIC_EXPORT=1 npx next build
)
echo "[OK] Frontend built -> web/out/"
echo

# ---- Step 2: Version assets ----
echo "[2/5] Versioning static asset URLs..."
node scripts/version-assets.mjs web/out
echo "[OK] Asset versioning done"
echo

# ---- Step 3: Copy to embed dir ----
echo "[3/5] Copying static output to api/webui/dist ..."
rm -rf api/webui/dist
mkdir -p api/webui/dist
cp -r web/out/* api/webui/dist/
echo "[OK] Copied to api/webui/dist/"
echo

# ---- Step 4: Build Go binary ----
echo "[4/5] Building Go API binary (with embedded frontend)..."
(
    cd api
    go build -o live2d-api .
)
echo "[OK] Go binary ready: api/live2d-api"
echo

# ---- Step 5: Start Tauri ----
echo "[5/5] Starting Tauri dev window..."
echo
echo "  ================================================"
echo "   Tauri window will open at http://localhost:8080"
echo "   Close window = automatically stop Go backend"
echo "  ================================================"
echo

cd desktop
cargo tauri dev
