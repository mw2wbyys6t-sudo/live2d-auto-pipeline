@echo off
chcp 65001 >nul
echo 🎭 Live2D Master Agent v10.0 Installer
echo ========================================
echo.

REM Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python 3.9+ is required. Please install from https://python.org
    pause
    exit /b 1
)

python --version
echo.

REM Create virtual environment (optional)
if not exist ".venv" (
    set /p CREATE_VENV="Create virtual environment? [Y/n] "
    if /i not "%CREATE_VENV%"=="n" (
        python -m venv .venv
        call .venv\Scripts\activate.bat
        echo ✓ Virtual environment created and activated
    )
)

REM Run Python installer
python install.py %*

echo.
echo 🎉 Installation complete!
echo.
if exist ".venv" (
    echo If using venv, activate it first:
    echo   .venv\Scripts\activate
)
echo.
echo Quick start:
echo   python -m core.workflow "蓝发猫耳少女" --deploy-desktop
echo.
pause
