@echo off
REM ======================================================================
REM Live2D Master Agent - Tauri 开发模式一键启动（完整构建链）
REM
REM 用法：双击运行或命令行 scripts\run_tauri_dev.bat
REM 前提：已安装 Rust + cargo install tauri-cli --version "^2"
REM       已安装 Node.js + 已在 web\ 下执行过 npm install
REM
REM 构建链（与 build_desktop.bat 一致）：
REM   1. Next.js 静态导出 → web\out\
REM   2. 版本化资源 URL（防缓存）
REM   3. 复制到 api\webui\dist\
REM   4. 编译 Go 二进制（go:embed 内嵌前端）
REM   5. 启动 Tauri 开发窗口（自动拉起 Go 后端，打开原生窗口）
REM ======================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

echo ==========================================
echo  Live2D Master Agent - Tauri 桌面版启动
echo ==========================================
echo.

REM ---- 检查前提 ----
echo [0/5] 检查前提条件...

where cargo >nul 2>&1
if errorlevel 1 (echo [X] cargo 未找到，请先安装 Rust & exit /b 1)

cargo tauri --version >nul 2>&1
if errorlevel 1 (
    echo [!] tauri-cli 未安装，正在安装...
    cargo install tauri-cli --version "^2"
    if errorlevel 1 (echo [X] tauri-cli 安装失败 & exit /b 1)
)

if not exist "web\node_modules" (
    echo [!] web\node_modules 不存在，正在安装前端依赖...
    pushd web
    call npm install
    if errorlevel 1 (popd & echo [X] npm install 失败 & exit /b 1)
    popd
)

echo [OK] 前提条件满足
echo.

REM ---- 第 1 步：构建前端 ----
echo [1/5] 构建前端静态站点 NEXT_STATIC_EXPORT=1 ...
pushd web
set NEXT_STATIC_EXPORT=1
call npm run build
if errorlevel 1 (popd & echo [X] 前端构建失败 & exit /b 1)
set NEXT_STATIC_EXPORT=
popd
echo [OK] 前端构建完成 -^> web\out\
echo.

REM ---- 第 2 步：版本化资源 URL ----
echo [2/5] 版本化静态资源 URL（防升级后浏览器缓存错版）...
node scripts\version-assets.mjs web\out
if errorlevel 1 (echo [X] 资源版本化失败 & exit /b 1)
echo [OK] 资源版本化完成
echo.

REM ---- 第 3 步：复制到内嵌目录 ----
echo [3/5] 复制静态产物到 api\webui\dist ...
if exist api\webui\dist rmdir /s /q api\webui\dist
mkdir api\webui\dist 2>nul
robocopy web\out api\webui\dist /E /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (echo [X] 静态产物复制失败 & exit /b 1)
echo [OK] 已复制到 api\webui\dist\
echo.

REM ---- 第 4 步：编译 Go 二进制 ----
echo [4/5] 编译 Go API 二进制（内嵌前端）...
pushd api
go build -o live2d-api.exe .
if errorlevel 1 (popd & echo [X] Go 编译失败 & exit /b 1)
popd
echo [OK] Go 二进制已生成 api\live2d-api.exe
echo.

REM ---- 第 5 步：启动 Tauri ----
echo [5/5] 启动 Tauri 开发窗口...
echo.
echo   ════════════════════════════════════════════════
echo    Tauri 窗口即将打开，加载 http://localhost:8080
echo    关闭窗口 = 自动停止 Go 后端
echo   ════════════════════════════════════════════════
echo.

REM 先杀掉可能残留的旧进程
taskkill /F /IM live2d-api.exe >nul 2>&1

pushd desktop
cargo tauri dev
if errorlevel 1 (popd & echo [X] Tauri 启动失败 & exit /b 1)
popd

endlocal
