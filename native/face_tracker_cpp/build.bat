@echo off
REM Build C++ face tracker with MSVC BuildTools.
REM Usage: build.bat   (output: build\face_tracker.exe)
setlocal

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if exist "%VCVARS%" goto :vcvars_ok
echo [error] vcvars64.bat not found: "%VCVARS%"
exit /b 1
:vcvars_ok
call "%VCVARS%" >nul
cd /d "%~dp0"
if not exist build mkdir build

cl /nologo /std:c++17 /O2 /EHsc /utf-8 /W3 /MT /D_CRT_SECURE_NO_WARNINGS ^
   /I third_party /I src ^
   src\main.cpp src\camera.cpp src\ort.cpp src\models.cpp src\mapping.cpp ^
   /Fe:build\face_tracker.exe /Fo:build\ ^
   /link /SUBSYSTEM:CONSOLE mfplat.lib mfuuid.lib mfreadwrite.lib mf.lib ole32.lib user32.lib shell32.lib
if errorlevel 1 goto :build_fail

REM onnxruntime.dll must sit next to the exe (dynamic loading).
if exist build\onnxruntime.dll goto :done
if not exist C:\Python314\Lib\site-packages\onnxruntime\capi\onnxruntime.dll goto :dll_warn
copy /y C:\Python314\Lib\site-packages\onnxruntime\capi\onnxruntime.dll build\ >nul
echo [ok] onnxruntime.dll copied from local pip package
goto :done
:dll_warn
echo [warn] build\onnxruntime.dll missing - run download_deps.ps1
goto :done
:build_fail
echo [error] build failed
exit /b 1
:done
echo [ok] build\face_tracker.exe
endlocal