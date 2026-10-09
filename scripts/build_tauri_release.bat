@echo off
REM ======================================================================
REM Live2D Master Agent - Tauri 发布版构建（一键打包 NSIS 安装包）
REM
REM 用法：双击运行或命令行 scripts\build_tauri_release.bat
REM 前提：已安装 Rust + tauri-cli + Node.js + Go
REM
REM 产物：desktop\target\release\bundle\nsis\*-setup.exe
REM       （这是最终用户双击安装的安装包）
REM ======================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

echo ==========================================
echo  Live2D Master Agent - 发布版构建
echo ==========================================
echo.

REM ---- 检查前提 ----
echo [0/6] 检查前提条件...

where cargo >nul 2>&1
if errorlevel 1 (echo [X] cargo 未找到，请先安装 Rust & goto :FAILED)

cargo tauri --version >nul 2>&1
if errorlevel 1 (
    echo [!] tauri-cli 未安装，正在安装...
    cargo install tauri-cli --version "^2"
    if errorlevel 1 (echo [X] tauri-cli 安装失败 & goto :FAILED)
)

where node >nul 2>&1
if errorlevel 1 (echo [X] Node.js 未安装 & goto :FAILED)

where go >nul 2>&1
if errorlevel 1 (echo [X] Go 未安装 & goto :FAILED)

if not exist "web\node_modules" (
    echo [!] 前端依赖未安装，正在安装...
    pushd web
    call npm install
    if errorlevel 1 (popd & echo [X] npm install 失败 & goto :FAILED)
    popd
)

echo [OK] 前提条件满足
echo.

REM ---- 第 1 步 ----
echo [1/6] 构建前端静态站点 NEXT_STATIC_EXPORT=1 ...
pushd web
set NEXT_STATIC_EXPORT=1
call npm run build
if errorlevel 1 (popd & echo [X] 前端构建失败 & goto :FAILED)
set NEXT_STATIC_EXPORT=
popd
echo [OK] web\out\
echo.

REM ---- 第 2 步 ----
echo [2/6] 版本化静态资源 URL ...
node scripts\version-assets.mjs web\out
if errorlevel 1 (echo [X] 资源版本化失败 & goto :FAILED)
echo [OK]
echo.

REM ---- 第 3 步 ----
echo [3/6] 复制到 api\webui\dist ...
if exist api\webui\dist rmdir /s /q api\webui\dist
mkdir api\webui\dist 2>nul
robocopy web\out api\webui\dist /E /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (echo [X] 复制失败 & goto :FAILED)
echo [OK]
echo.

REM ---- 第 4 步 ----
echo [4/6] 编译 Go 二进制（内嵌前端，windowsgui 无控制台）...
taskkill /F /IM live2d-api.exe >nul 2>&1
pushd api
go build -trimpath -ldflags "-s -w -H windowsgui" -o live2d-api.exe .
if errorlevel 1 (popd & echo [X] Go 编译失败 & goto :FAILED)
popd
echo [OK] api\live2d-api.exe
echo.

REM ---- 第 5 步 ----
echo [5/6] 复制 Go 二进制到 desktop\bin\（Tauri resources）...
if not exist desktop\bin mkdir desktop\bin
copy /Y api\live2d-api.exe desktop\bin\live2d-api.exe >nul
if errorlevel 1 (echo [X] 复制失败 & goto :FAILED)
echo [OK] desktop\bin\live2d-api.exe
echo.

REM ---- 第 6 步 ----
echo [6/6] cargo tauri build（编译 Rust release + 打包 NSIS）...
echo.
echo   首次构建约需 5-10 分钟
echo   如果 NSIS 首次使用，Tauri 会自动下载编译器（需要网络）
echo.

pushd desktop
cargo tauri build --bundles nsis
if errorlevel 1 (popd & echo [X] Tauri 构建失败 & goto :FAILED)
popd

echo.
echo ==========================================
echo  构建成功！
echo ==========================================
echo.
echo  安装包位置：
echo    desktop\target\release\bundle\nsis\*.exe
echo.
echo  把安装包发给用户，双击即可安装运行。
echo ==========================================
goto :END

:FAILED
echo.
echo ==========================================
echo  构建失败！
echo ==========================================
echo  请将上方错误信息截图发给开发者
echo ==========================================

:END
echo.
pause
endlocal
