#!/usr/bin/env bash
set -euo pipefail

# Live2D Master Agent - Tauri release build (produces installer)
cd "$(dirname "$0")/.."

echo "=========================================="
echo " Live2D Master Agent - Release Build"
echo "=========================================="
echo

# ---- Prerequisites ----
echo "[0/6] Checking prerequisites..."
command -v cargo >/dev/null || { echo "[X] cargo not found"; exit 1; }
command -v node  >/dev/null || { echo "[X] node not found";  exit 1; }
command -v go    >/dev/null || { echo "[X] go not found";    exit 1; }

if ! cargo tauri --version >/dev/null 2>&1; then
    echo "[!] tauri-cli not found, installing..."
    cargo install tauri-cli --version "^2"
fi

if [ ! -d "web/node_modules" ]; then
    echo "[!] Installing frontend deps..."
    (cd web && npm install)
fi
echo "[OK] Prerequisites satisfied"
echo

# ---- Step 1: Frontend ----
echo "[1/6] Building frontend static export..."
(cd web && NEXT_STATIC_EXPORT=1 npx next build)
echo "[OK] web/out/"
echo

# ---- Step 2: Version assets ----
echo "[2/6] Versioning asset URLs..."
node scripts/version-assets.mjs web/out
echo "[OK]"
echo

# ---- Step 3: Copy to embed dir ----
echo "[3/6] Copying to api/webui/dist ..."
rm -rf api/webui/dist
mkdir -p api/webui/dist
cp -r web/out/* api/webui/dist/
echo "[OK]"
echo

# ---- Step 4: Go binary ----
echo "[4/6] Building Go binary (with embedded frontend)..."
(cd api && go build -trimpath -ldflags "-s -w" -o live2d-api .)
echo "[OK] api/live2d-api"
echo

# ---- Step 5: Copy to Tauri resources ----
echo "[5/6] Copying Go binary to desktop/bin/..."
mkdir -p desktop/bin
cp api/live2d-api "desktop/bin/live2d-api"
echo "[OK] desktop/bin/live2d-api"
echo

# ---- Step 6: Tauri build ----
echo "[6/6] cargo tauri build (release + NSIS/AppImage)..."
echo
echo "  First build may take 5-10 minutes"
echo

cd desktop
cargo tauri build

echo
echo "=========================================="
echo " Build complete!"
echo "=========================================="
echo
echo "  Installer: desktop/src-tauri/target/release/bundle/"
echo "=========================================="
