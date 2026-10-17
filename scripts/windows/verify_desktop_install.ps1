# =====================================================================
#  Live2D Master Agent - post-install acceptance check (Windows)
#
#  WHAT IT DOES
#    Inspects the installed app, runs the bundled Python interpreter,
#    and queries the local API. Read-only: it changes nothing and only
#    writes a report to %TEMP%\live2d-acceptance-report.txt.
#
#  WHAT IT CANNOT DO
#    It cannot install the app and cannot press buttons in the UI.
#    The generate / layers / export / preview / pet steps are manual.
#
#  USAGE
#    powershell -NoProfile -ExecutionPolicy Bypass -File verify_desktop_install.ps1
#    powershell -NoProfile -ExecutionPolicy Bypass -File verify_desktop_install.ps1 -Launch
#    powershell -NoProfile -ExecutionPolicy Bypass -File verify_desktop_install.ps1 -InstallDir "D:\Apps\Live2D Master Agent"
#
#  -Launch   starts the app, waits up to 40s for http://127.0.0.1:8080,
#            then runs the API checks. Close the app when you are done.
#
#  ASCII-only on purpose: Windows consoles may be cp936/cp1252 and would
#  mangle non-ASCII text (same rule as check_desktop_env.ps1).
# =====================================================================

param(
    [string]$InstallDir = '',
    [switch]$Launch,
    [switch]$SkipTorch
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

$script:Report = New-Object System.Collections.ArrayList
$script:Fail   = 0
$script:Warn   = 0

function Add-Line {
    param([string]$Text = '')
    [void]$script:Report.Add($Text)
    Write-Host $Text
}

function Check {
    param([string]$Label, [string]$Status, [string]$Detail = '')
    $mark = '[ok]  '
    if ($Status -eq 'FAIL') { $mark = '[FAIL]'; $script:Fail++ }
    elseif ($Status -eq 'WARN') { $mark = '[WARN]'; $script:Warn++ }
    elseif ($Status -eq 'INFO') { $mark = '[info]' }
    Add-Line ("{0} {1,-28} {2}" -f $mark, $Label, $Detail)
}

Add-Line '====================================================================='
Add-Line ' Live2D Master Agent - post-install acceptance'
Add-Line (" Time: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Add-Line '====================================================================='
Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- 1. installation ---'
# ---------------------------------------------------------------------
# The Tauri shell keeps the Cargo package name (live2d-master-agent.exe);
# some builds used the product name instead. Accept either.
function Get-ShellExe {
    param([string]$Dir)
    foreach ($name in @('live2d-master-agent.exe', 'Live2D Master Agent.exe')) {
        $candidate = Join-Path $Dir $name
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

if (-not $InstallDir) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Live2D Master Agent'),
        (Join-Path $env:ProgramFiles 'Live2D Master Agent')
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} 'Live2D Master Agent')
    }
    foreach ($p in $candidates) {
        if (Get-ShellExe $p) { $InstallDir = $p; break }
    }
}

if (-not $InstallDir -or -not (Test-Path $InstallDir)) {
    Check 'install dir' 'FAIL' 'not found; pass -InstallDir "<path>"'
    Add-Line ''
    Add-Line 'Hint: right-click the desktop shortcut > Properties to see the target path.'
    $InstallDir = ''
} else {
    Check 'install dir' 'INFO' $InstallDir
}

$need = @(
    'live2d-api.exe',
    'face_tracker.exe',
    'onnxruntime.dll',
    'models\face_detection_yunet_2023mar.onnx',
    'models\face_landmarker.task',
    'python\python.exe',
    'python\pythonw.exe',
    'python\Lib\site-packages\sitecustomize.py',
    'python\Lib\site-packages\PIL',
    'python\Lib\site-packages\numpy',
    'python\Lib\site-packages\torch',
    'python\Lib\site-packages\mediapipe',
    'python\Lib\site-packages\live2d\v3',
    'app\core\workflow.py',
    'app\drivers\desktop_pet',
    'BUILD-INFO.json'
)

if ($InstallDir) {
    $missing = @()
    foreach ($n in $need) {
        if (-not (Test-Path (Join-Path $InstallDir $n))) { $missing += $n }
    }
    $shell = Get-ShellExe $InstallDir
    if (-not $shell) { $missing += 'live2d-master-agent.exe' }

    if ($missing.Count -eq 0) {
        Check ("bundled files ({0})" -f ($need.Count + 1)) 'OK' 'all present'
    } else {
        Check 'bundled files' 'FAIL' ("missing: " + ($missing -join '; '))
    }

    $info = Join-Path $InstallDir 'BUILD-INFO.json'
    if (Test-Path $info) {
        try {
            $bi = Get-Content $info -Raw | ConvertFrom-Json
            Check 'BUILD-INFO python' 'OK' $bi.python_embed
            Check 'BUILD-INFO total' 'INFO' ("{0:N1} MB" -f ($bi.sizes_bytes.total / 1MB))
        } catch {
            Check 'BUILD-INFO' 'WARN' 'unreadable JSON'
        }
    }
}

Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- 2. bundled interpreter ---'
# ---------------------------------------------------------------------
$py = ''
if ($InstallDir) { $py = Join-Path $InstallDir 'python\python.exe' }

if (-not $py -or -not (Test-Path $py)) {
    Check 'python.exe' 'FAIL' 'not found under <install>\python'
} else {
    $ver = (& $py --version 2>&1 | Select-Object -First 1)
    if ("$ver" -match '3\.12\.') { Check 'python version' 'OK' "$ver" }
    else { Check 'python version' 'FAIL' "$ver" }

    $imports = (& $py -c "import PIL, numpy, cv2, scipy, sklearn, psd_tools, onnxruntime; print('IMPORTS_OK')" 2>&1)
    $importsTail = ($imports | Select-Object -Last 1)
    if ("$importsTail" -eq 'IMPORTS_OK') { Check 'core imports' 'OK' '7 modules' }
    else { Check 'core imports' 'FAIL' (($imports | Out-String).Trim()) }

    # mediapipe is checked separately so one bad wheel cannot mask the others.
    $mp = (& $py -c "import mediapipe; print('MP_OK', mediapipe.__version__)" 2>&1)
    $mpTail = ($mp | Select-Object -Last 1)
    if ("$mpTail" -like 'MP_OK*') { Check 'mediapipe' 'OK' "$mpTail" }
    else { Check 'mediapipe' 'WARN' (($mp | Out-String).Trim()) }

    $appImports = (& $py -c "import core.version, live2d_builder.pipeline; print('APP_IMPORTS_OK')" 2>&1)
    $appTail = ($appImports | Select-Object -Last 1)
    if ("$appTail" -eq 'APP_IMPORTS_OK') {
        Check 'app imports + sys.path' 'OK' 'sitecustomize works'
    } else {
        Check 'app imports + sys.path' 'FAIL' (($appImports | Out-String).Trim())
    }

    if (-not $SkipTorch) {
        $torch = (& $py -c "import torch; print('TORCH_OK', torch.__version__)" 2>&1)
        $torchTail = ($torch | Select-Object -Last 1)
        if ("$torchTail" -like 'TORCH_OK*') { Check 'torch' 'OK' "$torchTail" }
        else { Check 'torch' 'WARN' (($torch | Out-String).Trim()) }
    }
}

Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- 3. local API ---'
# ---------------------------------------------------------------------
function Get-Api {
    param([string]$Path)
    try {
        return (Invoke-RestMethod -Uri ("http://127.0.0.1:8080" + $Path) -TimeoutSec 8)
    } catch {
        return $null
    }
}

$health = Get-Api '/api/health'
if (-not $health -and $Launch -and $InstallDir) {
    $exe = Get-ShellExe $InstallDir
    if ($exe) {
        Add-Line ("launching: " + $exe)
        Start-Process -FilePath $exe | Out-Null
    } else {
        Add-Line 'launch skipped: shell exe not found'
    }
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        $health = Get-Api '/api/health'
        if ($health) { break }
    }
}

if ($health -and $health.success -eq $true) {
    $pyOk = $health.data.python_ok
    if ($pyOk -eq $true) { Check 'GET /api/health' 'OK' 'python_ok=true' }
    else { Check 'GET /api/health' 'FAIL' ("python_ok=" + $pyOk + " " + $health.error) }
} elseif ($health) {
    Check 'GET /api/health' 'FAIL' ("success=" + $health.success + " " + $health.error)
} else {
    Check 'GET /api/health' 'WARN' 'not reachable (start the app, or add -Launch)'
}

$diag = Get-Api '/api/diagnostics'
if ($diag -and $diag.data) {
    if ($diag.data.python_path) { Check 'diag python_path' 'INFO' $diag.data.python_path }
    if ($diag.data.output_dir)  { Check 'diag output_dir'  'INFO' $diag.data.output_dir }
    foreach ($c in $diag.data.checks) {
        $key = "" + $c.key
        if ($c.ok -eq $true) {
            Check ("check:" + $key) 'OK' $c.label
        } else {
            $issues = ''
            if ($c.issues) { $issues = ($c.issues -join ', ') }
            Check ("check:" + $key) 'FAIL' ($c.label + ' ' + $issues)
        }
    }
} else {
    Check 'GET /api/diagnostics' 'WARN' 'not reachable'
}

Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- 4. processes (informational) ---'
# ---------------------------------------------------------------------
Add-Line 'Close the app first if you want to verify process cleanup.'
$procs = @()
$names = @('live2d-api', 'face_tracker', 'python', 'pythonw')
foreach ($n in $names) {
    $found = Get-Process -Name $n -ErrorAction SilentlyContinue
    if ($found) { $procs += $found }
}
if ($procs.Count -eq 0) {
    Check 'running processes' 'OK' 'none'
} else {
    foreach ($p in $procs) {
        Check ('proc:' + $p.ProcessName) 'INFO' ("pid " + $p.Id)
    }
}

# ---------------------------------------------------------------------
Add-Line ''
Add-Line '--- 5. summary ---'
# ---------------------------------------------------------------------
if ($script:Fail -eq 0 -and $script:Warn -eq 0) {
    Add-Line 'RESULT: PASS'
} elseif ($script:Fail -eq 0) {
    Add-Line ("RESULT: PASS with {0} warning(s)" -f $script:Warn)
} else {
    Add-Line ("RESULT: FAIL ({0} failure(s), {1} warning(s))" -f $script:Fail, $script:Warn)
}
Add-Line ''
Add-Line 'Not covered here (must be checked in the UI):'
Add-Line '  - Generate / Layers / Export / Preview / Pet one by one'
Add-Line '  - Camera permission and face tracking'
Add-Line '  - Close the window and confirm no leftover processes'

$reportPath = Join-Path $env:TEMP 'live2d-acceptance-report.txt'
$script:Report | Out-File -FilePath $reportPath -Encoding UTF8
Add-Line ''
Add-Line ("Report saved to: " + $reportPath)
Add-Line 'Paste this report back if you need help diagnosing a failure.'
