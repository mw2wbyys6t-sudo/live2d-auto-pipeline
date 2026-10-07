@echo off
chcp 65001 >nul
echo 🎭 Live2D Master Agent Installer
echo ========================================
echo.

REM 预检 1：python 必须存在，否则不进入 install.py（避免用户看到 Python traceback）
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未检测到 Python。Live2D Master Agent 需要 Python 3.9+。
    echo.
    echo 请先安装 Python：
    echo   • 下载页：https://www.python.org/downloads/
    echo     安装时务必勾选 "Add Python to PATH"
    echo   • winget：  winget install Python.Python.3.12
    echo   • Chocolatey：choco install python
    echo.
    pause
    exit /b 1
)

REM 预检 2：python 版本 ≥ 3.9
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>nul
if %errorlevel% neq 0 (
    echo ❌ Python 版本过低，需要 3.9+。
    echo   当前版本：
    python --version
    echo   下载新版本：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

python --version
echo.

REM 可选：创建虚拟环境。把 set /p 与判断分开写，避免 cmd 在括号块内提前展开变量。
if exist ".venv" goto run_install
set /p CREATE_VENV="Create virtual environment? [Y/n] "
if /i "%CREATE_VENV%"=="n" goto run_install
python -m venv .venv
call .venv\Scripts\activate.bat
echo ✓ Virtual environment created and activated

:run_install
REM 运行 Python 安装器（透传所有参数：--check / --dry-run / --minimal / --yes 等）
python install.py %*
set INSTALL_RC=%errorlevel%

echo.
if %INSTALL_RC%==0 echo 🎉 Installation complete!
if %INSTALL_RC%==1 echo ⚠ 环境检测发现缺失项（退出码 1）。运行 install.py --check 查看详情。
if %INSTALL_RC%==2 echo ⚠ 部分安装步骤失败（退出码 2）。请查看上方输出与 docs\FAQ.md。
if not %INSTALL_RC%==0 if not %INSTALL_RC%==1 if not %INSTALL_RC%==2 echo ⚠ 安装未正常完成（退出码 %INSTALL_RC%）。
echo.
if exist ".venv" (
    echo If using venv, activate it first:
    echo   .venv\Scripts\activate
    echo.
)
echo Quick start:
echo   python -m core.workflow "蓝发猫耳少女" --deploy-desktop
echo   python start.py
echo   python install.py --check
echo   python install.py --dry-run
echo.
pause
exit /b %INSTALL_RC%
