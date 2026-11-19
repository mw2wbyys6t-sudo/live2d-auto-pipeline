# C++ face tracker dependency downloader (idempotent; existing files skipped).
# Usage: powershell -ExecutionPolicy Bypass -File download_deps.ps1
#
# Dependencies:
#   1. MediaPipe 468-landmark face mesh ONNX (Qualcomm AI Hub export)
#   2. ONNX Runtime C API header (v1.30.0, matches local pip onnxruntime.dll)
#   3. nlohmann/json single header (MIT)
#   4. onnxruntime.dll - reuse local pip package, else official release zip
#   5. YuNet face detection model - reuse project models/face/ copy

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root       = Split-Path -Parent $PSScriptRoot          # native/
$projRoot   = Split-Path -Parent $root                  # project root
$cppDir     = $PSScriptRoot
$thirdParty = Join-Path $cppDir 'third_party'
$buildDir   = Join-Path $cppDir 'build'
$modelDir   = Join-Path $projRoot 'models\face'

New-Item -ItemType Directory -Force -Path $thirdParty, $buildDir, $modelDir | Out-Null

function Download-IfMissing($url, $dest, $name) {
    if (Test-Path $dest) { Write-Host "[skip] $name exists"; return }
    Write-Host "[download] $name ..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        Write-Host "[ok] $name"
    } catch {
        Write-Host "[error] $name failed: $($_.Exception.Message)"
        Write-Host "        manual URL: $url"
        Write-Host "        save to:    $dest"
    }
}

# 1. 478-landmark face mesh model with irises (~4.6MB, Apache-2.0)
#    Source: https://github.com/yakhyo/mediapipe-face-mesh-onnx (MediaPipe port)
Download-IfMissing `
    'https://github.com/yakhyo/mediapipe-face-mesh-onnx/releases/download/weights/face_landmarker_Nx3x256x256.onnx' `
    (Join-Path $modelDir 'face_landmark_478.onnx') 'Face Landmarker 478 model'

# 2. ONNX Runtime C API headers (v1.30.0, matches local pip onnxruntime.dll).
#    GitHub raw tags 404 for this version; the official NuGet package carries
#    the exact headers under build/native/include.
$headerDest = Join-Path $thirdParty 'onnxruntime_c_api.h'
if (Test-Path $headerDest) {
    Write-Host '[skip] onnxruntime_c_api.h exists'
} else {
    Write-Host '[download] ONNX Runtime 1.30.0 headers (NuGet package)...'
    $tmp = Join-Path $env:TEMP 'ort_nuget_cpp.zip'
    $ext = Join-Path $env:TEMP 'ort_nuget_cpp'
    try {
        Invoke-WebRequest -Uri 'https://www.nuget.org/api/v2/package/Microsoft.ML.OnnxRuntime/1.30.0' -OutFile $tmp -UseBasicParsing
        Expand-Archive -Path $tmp -DestinationPath $ext -Force
        Copy-Item (Join-Path $ext 'build\native\include\*') $thirdParty -Force
        Write-Host '[ok] ONNX Runtime headers'
    } catch {
        Write-Host "[error] headers failed: $($_.Exception.Message)"
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        Remove-Item $ext -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# 3. nlohmann/json
Download-IfMissing `
    'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp' `
    (Join-Path $thirdParty 'json.hpp') 'nlohmann/json v3.11.3'

# 3b. stb_image (self-test --image mode decoder; public domain)
Download-IfMissing `
    'https://raw.githubusercontent.com/nothings/stb/master/stb_image.h' `
    (Join-Path $thirdParty 'stb_image.h') 'stb_image.h'

# 4. onnxruntime.dll -> build/ (next to exe)
$dllDest = Join-Path $buildDir 'onnxruntime.dll'
if (Test-Path $dllDest) {
    Write-Host '[skip] onnxruntime.dll exists'
} else {
    $pipDll = 'C:\Python314\Lib\site-packages\onnxruntime\capi\onnxruntime.dll'
    if (Test-Path $pipDll) {
        Copy-Item $pipDll $dllDest -Force
        Write-Host '[ok] onnxruntime.dll (copied from local pip package)'
    } else {
        Write-Host '[download] onnxruntime.dll (official zip, large)...'
        $tmp = Join-Path $env:TEMP 'onnxruntime_cpp.zip'
        $ext = Join-Path $env:TEMP 'ort_extract_cpp'
        try {
            Invoke-WebRequest -Uri 'https://github.com/microsoft/onnxruntime/releases/download/v1.30.0/onnxruntime-win-x64-1.30.0.zip' -OutFile $tmp -UseBasicParsing
            Expand-Archive -Path $tmp -DestinationPath $ext -Force
            $dll = Get-ChildItem $ext -Recurse -Filter onnxruntime.dll | Select-Object -First 1
            Copy-Item $dll.FullName $dllDest -Force
            Write-Host '[ok] onnxruntime.dll'
        } catch {
            Write-Host "[error] onnxruntime.dll failed: $($_.Exception.Message)"
        } finally {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            Remove-Item $ext -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# 5. YuNet model (reuse project copy; download if missing)
Download-IfMissing `
    'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx' `
    (Join-Path $modelDir 'face_detection_yunet_2023mar.onnx') 'YuNet face detection model'

Write-Host ''
Write-Host '=== dependency check ==='
foreach ($f in @(
    (Join-Path $modelDir 'face_landmark_478.onnx'),
    (Join-Path $modelDir 'face_detection_yunet_2023mar.onnx'),
    (Join-Path $thirdParty 'onnxruntime_c_api.h'),
    (Join-Path $thirdParty 'json.hpp'),
    (Join-Path $thirdParty 'stb_image.h'),
    $dllDest
)) {
    if (Test-Path $f) { Write-Host "  OK      $f" } else { Write-Host "  MISSING $f" }
}