@echo off
REM Live2D Master Agent 一键启动器（Windows）
REM 这是一个薄包装：真正的逻辑在 start.py 里。
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
    echo ❌ Python 未安装，请先安装 Python 3.9+：https://www.python.org/downloads/
    pause
    exit /b 1
)
python start.py %*
