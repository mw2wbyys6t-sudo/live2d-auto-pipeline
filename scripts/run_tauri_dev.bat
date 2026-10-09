@echo off
REM ======================================================================
REM Live2D Master Agent - Tauri 开发模式一键启动
REM
REM 用法：双击运行或命令行 scripts\run_tauri_dev.bat
REM 前提：已安装 Rust + cargo tauri-cli --version "^2"
REM
REM 流程：
REM   1. 编译 Go API 二进制到 api\live2d-api.exe
REM   2. 启动 Tauri 开发窗口（自动拉起 Go 后端，打开原生窗口）
REM ======================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

echo [1/3] 检查 tauri-cli ...
cargo tauri --version >nul 2>&1
if errorlevel 1 (
    echo [!] tauri-cli 未安装，正在安装...
    cargo install tauri-cli --version "^2"
    if errorlevel 1 (echo tauri-cli 安装失败 & exit /b 1)
)

echo [2/3] 编译 Go API 二进制 ...
pushd api
go build -o live2d-api.exe .
if errorlevel 1 (popd & echo Go 编译失败 & exit /b 1)
popd

echo [3/3] 启动 Tauri 开发窗口 ...
echo.
echo   Tauri 窗口即将打开，加载 http://localhost:8080
echo   关闭窗口 = 自动停止 Go 后端
echo   按 Ctrl+C 可强制退出
echo.
pushd desktop
cargo tauri dev
if errorlevel 1 (popd & echo Tauri 启动失败 & exit /b 1)
popd

endlocal
