@echo off
REM 下载 YuNet ONNX 人脸检测模型 + ONNX Runtime DLL
REM
REM 使用方法：
REM   cd native\face_tracker
REM   download_models.bat
REM
REM 文件会下载到 models/ 目录和 exe 同目录。

setlocal

set MODEL_DIR=%~dp0models
set RELEASE_DIR=%~dp0target\release

REM ── 创建目录 ──
if not exist "%MODEL_DIR%" mkdir "%MODEL_DIR%"

REM ── 1. YuNet 人脸检测模型 (~340KB) ──
set MODEL_FILE=%MODEL_DIR%\face_detection_yunet_2023mar.onnx

if exist "%MODEL_FILE%" (
    echo [skip] YuNet 模型已存在: %MODEL_FILE%
) else (
    echo [download] 正在下载 YuNet 人脸检测模型...
    REM OpenCV Zoo 官方源
    powershell -Command "try { Invoke-WebRequest -Uri 'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx' -OutFile '%MODEL_FILE%' -UseBasicParsing } catch { Write-Host '[fallback] 尝试备用源...'; Invoke-WebRequest -Uri 'https://github.com/DefangChen/YuNet/raw/main/model/yunet.onnx' -OutFile '%MODEL_FILE%' -UseBasicParsing }"
    if exist "%MODEL_FILE%" (
        echo [ok] YuNet 模型下载完成
    ) else (
        echo [error] YuNet 模型下载失败！请手动下载：
        echo        https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
        echo        保存到: %MODEL_FILE%
    )
)

REM ── 2. ONNX Runtime DLL (~7MB, load-dynamic 需要) ──
if not exist "%RELEASE_DIR%\face_tracker.exe" (
    echo [warn] face_tracker.exe 尚未编译，跳过 DLL 下载
    goto :done
)

set DLL_FILE=%RELEASE_DIR%\onnxruntime.dll

if exist "%DLL_FILE%" (
    echo [skip] onnxruntime.dll 已存在
) else (
    echo [download] 正在下载 ONNX Runtime DLL...
    REM ort 2.0.0-rc.14 的 ORT_API_VERSION = 17（即最低支持 ONNX Runtime 1.17），
    REM 这里钉 1.30.0 与 scripts/build_desktop_bundle.py、download_deps.ps1 一致。
    REM 注意：GitHub release 并未提供 win-x64 的 zip（v1.30.0/v1.31.0 实测 404），
    REM 改从 NuGet 官方包取 win-x64/native/onnxruntime.dll（包里另有 arm64，不能递归找）。
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; $url = 'https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime/1.30.0/microsoft.ml.onnxruntime.1.30.0.nupkg'; $tmp = Join-Path $env:TEMP 'onnxruntime.nupkg'; $ext = Join-Path $env:TEMP 'ort_extract'; Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing; Expand-Archive -Path $tmp -DestinationPath $ext -Force; Copy-Item (Join-Path $ext 'runtimes\win-x64\native\onnxruntime.dll') '%DLL_FILE%' -Force; Remove-Item $tmp -Force; Remove-Item $ext -Recurse -Force"
    if exist "%DLL_FILE%" (
        echo [ok] onnxruntime.dll 下载完成
    ) else (
        echo [error] ONNX Runtime DLL 下载失败！请手动下载：
        echo        https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime/1.30.0/microsoft.ml.onnxruntime.1.30.0.nupkg
        echo        解压后取 runtimes\win-x64\native\onnxruntime.dll 放到: %DLL_FILE%
    )
)

:done
echo.
echo === 下载完成 ===
if exist "%MODEL_FILE%" (echo   YuNet 模型: OK) else (echo   YuNet 模型: MISSING)
if exist "%RELEASE_DIR%\face_tracker.exe" (
    if exist "%DLL_FILE%" (echo   ONNX Runtime: OK) else (echo   ONNX Runtime: MISSING)
) else (
    echo   face_tracker.exe: 未编译 ^(先运行 cargo build --release^)
)
echo.
echo 提示：模型放到 models/ 目录后，face_tracker 启动时会自动检测并切换到 ONNX 模式。
pause
