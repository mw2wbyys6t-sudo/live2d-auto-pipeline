@echo off
REM ======================================================================
REM Live2D Master Agent - 桌面版一键构建
REM 产物：dist\Live2DMasterAgent.exe（单文件，内嵌 Web 工作台 + API）
REM 用法：双击 exe 或命令行启动；推荐把 exe 放在项目根目录（自动发现
REM       Python 工程与输出目录），或用 -config 指定配置文件。
REM ======================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

echo [1/5] 构建前端静态站点（NEXT_STATIC_EXPORT=1）...
pushd web
set NEXT_STATIC_EXPORT=1
call npm run build
if errorlevel 1 (popd & echo 前端构建失败 & exit /b 1)
popd

echo [2/5] 版本化静态资源 URL（防升级后浏览器缓存错版）...
node scripts\version-assets.mjs web\out
if errorlevel 1 (echo 资源版本化失败 & exit /b 1)

echo [3/5] 复制静态产物到内嵌目录 api\webui\dist ...
if exist api\webui\dist rmdir /s /q api\webui\dist
mkdir api\webui\dist 2>nul
robocopy web\out api\webui\dist /E /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (echo 静态产物复制失败 & exit /b 1)

echo [4/5] 编译单文件桌面程序（内嵌 UI，windowsgui 无控制台）...
taskkill /F /IM Live2DMasterAgent.exe >nul 2>&1
pushd api
go build -trimpath -ldflags "-s -w -H windowsgui" -o ..\dist\Live2DMasterAgent.exe .
if errorlevel 1 (popd & echo Go 编译失败 & exit /b 1)
rem 调试版：带控制台，便于查看 Python 桥接日志
go build -trimpath -o ..\dist\Live2DMasterAgent-console.exe .
if errorlevel 1 (popd & echo Go 编译失败(调试版) & exit /b 1)
popd

echo [5/5] 完成。
echo   桌面版: dist\Live2DMasterAgent.exe        （双击运行，无控制台）
echo   调试版: dist\Live2DMasterAgent-console.exe（带控制台日志）
echo 提示：把 exe 放在项目根目录可直接发现 Python 工程；否则用
echo       Live2DMasterAgent.exe -config 你的配置.json 指定 scripts_dir。
endlocal
