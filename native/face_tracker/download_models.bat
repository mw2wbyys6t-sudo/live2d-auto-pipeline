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
    REM 必须与 ort crate 匹配：ort 2.0.0-rc.14 要求 ONNX Runtime >= 1.31.x
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; $url = 'https://github.com/microsoft/onnxruntime/releases/download/v1.31.0/onnxruntime-win-x64-1.31.0.zip'; $tmp = '$env:TEMP\onnxruntime.zip'; Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing; Expand-Archive -Path $tmp -DestinationPath '$env:TEMP\ort_extract' -Force; $dll = Get-ChildItem '$env:TEMP\ort_extract' -Recurse -Filter onnxruntime.dll | Select-Object -First 1; Copy-Item $dll.FullName '%DLL_FILE%' -Force; Remove-Item $tmp -Force; Remove-Item '$env:TEMP\ort_extract' -Recurse -Force"
    if exist "%DLL_FILE%" (
        echo [ok] onnxruntime.dll 下载完成
    ) else (
        echo [error] ONNX Runtime DLL 下载失败！请手动下载：
        echo        https://github.com/microsoft/onnxruntime/releases/download/v1.31.0/onnxruntime-win-x64-1.31.0.zip
        echo        解压后将 onnxruntime.dll 放到: %DLL_FILE%
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
