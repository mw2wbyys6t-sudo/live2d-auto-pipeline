# =====================================================================
#  Live2D Master Agent - local environment check
#
#  Read-only by design: this script installs nothing and changes nothing.
#  It only looks at what is already on the machine.
#
#  ASCII-only on purpose: Windows consoles and Notepad re-encoding would
#  otherwise mangle non-ASCII text depending on the code page.
#
#  Run:
#    powershell -NoProfile -ExecutionPolicy Bypass -File check_desktop_env.ps1
#
#  Writes desktop-env-report.txt next to this script (UTF-8) and prints
#  the same content, so the output can be sent back for analysis.
# =====================================================================

$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference    = 'SilentlyContinue'

$script:Report = New-Object System.Collections.ArrayList

function Add-Line {
    param([string]$Text = '')
    [void]$script:Report.Add($Text)
    Write-Host $Text
}

function Write-Check {
    param(
        [string]$Label,
        [string]$Status,   # OK / MISSING / WARN / INFO
        [string]$Detail
    )
    $mark = '[--]'
    if ($Status -eq 'OK')      { $mark = '[ok]' }
    if ($Status -eq 'WARN')    { $mark = '[!!]' }
    if ($Status -eq 'INFO')    { $mark = '[..]' }
    Add-Line ("{0} {1,-12} {2}" -f $mark, $Label, $Detail)
}

# Run an external command and return its first output line, or $null when
# the executable does not exist at all.
function Get-CommandLine {
    param(
        [string]$Exe,
        [string[]]$ExeArgs = @()
    )
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $null }
    $out = & $Exe @ExeArgs 2>&1 | Select-Object -First 1
    if ($null -eq $out) { return '' }
    return ([string]$out).Trim()
}

# Windows ships a "python.exe" stub that just opens the Microsoft Store when
# Python is not really installed. Treat those messages as "not installed".
function Test-StoreStub {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
    return ($Text -match 'Microsoft Store' -or $Text -match 'App Execution Aliases' -or $Text -match 'was not found')
}

# The stub also lives under WindowsApps. Checking the path lets us skip it
# WITHOUT executing it, so we never pop open the Microsoft Store by accident.
function Test-StoreStubPath {
    param([string]$Exe)
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $cmd -or -not $cmd.Source) { return $false }
    return ($cmd.Source -like '*\WindowsApps\*')
}

$onWindows = ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT)

Add-Line '====================================================================='
Add-Line ' Live2D Master Agent - local environment report'
Add-Line (" Time: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Add-Line '====================================================================='
Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- Operating system ---'
# ---------------------------------------------------------------------
if ($onWindows) {
    $os = Get-CimInstance Win32_OperatingSystem
    Write-Check 'Windows' 'INFO' ("{0} (build {1})" -f $os.Caption, $os.BuildNumber)
} else {
    Write-Check 'OS' 'INFO' ([System.Environment]::OSVersion.VersionString)
}
Write-Check 'Arch' 'INFO' ("{0} / 64-bit process: {1}" -f $env:PROCESSOR_ARCHITECTURE, [System.Environment]::Is64BitProcess)

# Free space on the drive that holds this script - build tools need several GB.
if ($onWindows) {
    try {
        $qualifier = Split-Path -Qualifier (Resolve-Path $PSScriptRoot).Path
        $driveName = $qualifier.TrimEnd(':')
        $d = Get-PSDrive -Name $driveName
        $freeGB = [math]::Round($d.Free / 1GB, 1)
        $status = 'OK'
        if ($freeGB -lt 10) { $status = 'WARN' }
        Write-Check 'Free disk' $status ("{0} GB free on {1}" -f $freeGB, $qualifier)
    } catch {
        Write-Check 'Free disk' 'WARN' 'could not determine'
    }
}
Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- Core runtime (needed to RUN the app) ---'
# ---------------------------------------------------------------------
$pythonExe = $null
$pythonArgs = @()
$pythonVer = $null

# py -3 first: on Windows the py launcher is the most reliable sign that a
# real Python (not the Store stub) is installed. Fall back to python/python3.
$candidates = @(
    @{ Exe = 'py';      CheckArgs = @('-3', '--version'); RunArgs = @('-3') },
    @{ Exe = 'python';  CheckArgs = @('--version');      RunArgs = @() },
    @{ Exe = 'python3'; CheckArgs = @('--version');      RunArgs = @() }
)
foreach ($cand in $candidates) {
    if (-not (Get-Command $cand.Exe -ErrorAction SilentlyContinue)) { continue }
    if (Test-StoreStubPath $cand.Exe) { continue }
    $out = & $cand.Exe @($cand.CheckArgs) 2>&1 | Select-Object -First 1
    if ($null -eq $out) { continue }
    $out = ([string]$out).Trim()
    if (Test-StoreStub $out) { continue }
    $pythonExe  = $cand.Exe
    $pythonArgs = $cand.RunArgs
    $pythonVer  = $out
    break
}

if ($pythonExe) {
    Write-Check 'Python' 'OK' ("{0}  (command: {1})" -f $pythonVer, $pythonExe)
    Write-Check '  location' 'INFO' ((Get-Command $pythonExe -ErrorAction SilentlyContinue).Source)

    # pip
    $pip = & $pythonExe @pythonArgs '-m' 'pip' '--version' 2>&1 | Select-Object -First 1
    if ($pip) { Write-Check '  pip' 'OK' ([string]$pip) } else { Write-Check '  pip' 'MISSING' 'run: python -m ensurepip' }

    # The two libraries the Go health probe checks. The Python snippet is
    # deliberately quote-free: PowerShell mangles embedded double quotes when
    # passing arguments to a native command, which turns valid code into a
    # SyntaxError. The exit code is the reliable signal.
    & $pythonExe @pythonArgs '-c' 'import PIL, numpy' 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $verOut  = & $pythonExe @pythonArgs '-c' 'import PIL, numpy; print(PIL.__version__, numpy.__version__)' 2>&1
        $verCode = $LASTEXITCODE
        $verText = ''
        if ($verCode -eq 0 -and $verOut) {
            $verText = ([string]($verOut | Select-Object -First 1)).Trim()
        }
        if (-not $verText) { $verText = 'both importable' }
        Write-Check '  Pillow+numpy' 'OK' $verText
    } else {
        Write-Check '  Pillow+numpy' 'MISSING' 'run: python -m pip install -r requirements.txt'
    }
} else {
    Write-Check 'Python' 'MISSING' 'not installed (or only the Microsoft Store stub)'
}
Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- Build toolchain (needed to BUILD) ---'
# ---------------------------------------------------------------------
$node = Get-CommandLine 'node' @('--version')
if ($node) { Write-Check 'Node.js' 'OK' $node } else { Write-Check 'Node.js' 'MISSING' 'needed to build the frontend' }

$npm = Get-CommandLine 'npm' @('--version')
if ($npm) { Write-Check 'npm' 'OK' $npm } else { Write-Check 'npm' 'MISSING' 'comes with Node.js' }

$go = Get-CommandLine 'go' @('version')
if ($go) { Write-Check 'Go' 'OK' $go } else { Write-Check 'Go' 'MISSING' 'needed to build the API / desktop exe' }

$git = Get-CommandLine 'git' @('--version')
if ($git) { Write-Check 'Git' 'OK' $git } else { Write-Check 'Git' 'MISSING' 'needed to clone / update the repo' }
Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- Tauri toolchain (needed for the desktop window shell) ---'
# ---------------------------------------------------------------------
$rustc = Get-CommandLine 'rustc' @('--version')
if ($rustc) { Write-Check 'rustc' 'OK' $rustc } else { Write-Check 'rustc' 'MISSING' 'install Rust from rustup.rs' }

$cargo = Get-CommandLine 'cargo' @('--version')
if ($cargo) { Write-Check 'cargo' 'OK' $cargo } else { Write-Check 'cargo' 'MISSING' 'comes with Rust' }

$rustup = Get-CommandLine 'rustup' @('--version')
if ($rustup) { Write-Check 'rustup' 'OK' $rustup } else { Write-Check 'rustup' 'MISSING' 'recommended way to manage Rust' }

$tauriOut  = $null
$tauriCode = 1
if (Get-Command 'cargo' -ErrorAction SilentlyContinue) {
    $tauriOut  = & cargo tauri --version 2>&1 | Select-Object -First 1
    $tauriCode = $LASTEXITCODE
}
# Match a version NUMBER, not the word "tauri": when the CLI is missing, cargo
# prints "error: no such command: `tauri`", which contains the name and would
# otherwise be mistaken for a successful check.
if ($tauriCode -eq 0 -and $tauriOut -and ([string]$tauriOut -match '\d+\.\d+')) {
    Write-Check 'tauri-cli' 'OK' ([string]$tauriOut)
} else {
    Write-Check 'tauri-cli' 'MISSING' 'run: cargo install tauri-cli --version "^2"'
}

if ($onWindows) {
    # MSVC C++ build tools: Tauri on Windows needs them. vswhere is the
    # supported way to ask Visual Studio what is actually installed.
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $vsName = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property displayName 2>$null | Select-Object -First 1
        if ($vsName) {
            Write-Check 'MSVC C++' 'OK' ([string]$vsName)
        } else {
            Write-Check 'MSVC C++' 'MISSING' 'Visual Studio installed but without "Desktop development with C++"'
        }
    } else {
        Write-Check 'MSVC C++' 'MISSING' 'need VS Build Tools with "Desktop development with C++"'
    }

    # WebView2 runtime: Tauri renders the UI inside it.
    $webviewKeys = @(
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        'HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    )
    $webviewVer = $null
    foreach ($k in $webviewKeys) {
        $pv = (Get-ItemProperty -Path $k -Name 'pv' -ErrorAction SilentlyContinue).pv
        if ($pv) { $webviewVer = $pv; break }
    }
    if ($webviewVer) {
        Write-Check 'WebView2' 'OK' $webviewVer
    } else {
        Write-Check 'WebView2' 'MISSING' 'install the Evergreen WebView2 Runtime'
    }
}
Add-Line ''

# ---------------------------------------------------------------------
Add-Line '--- Project checkout ---'
# ---------------------------------------------------------------------
$marker = $null
$probe = $PSScriptRoot
for ($i = 0; $i -lt 4 -and $probe; $i++) {
    $candidateMarker = Join-Path $probe 'core\workflow.py'
    if (-not $onWindows) { $candidateMarker = Join-Path $probe 'core/workflow.py' }
    if (Test-Path $candidateMarker) { $marker = $probe; break }
    $parent = Split-Path -Parent $probe
    if ($parent -eq $probe) { break }
    $probe = $parent
}
if ($marker) {
    Write-Check 'Repo' 'OK' ("found at {0}" -f $marker)
} else {
    Write-Check 'Repo' 'MISSING' 'this script is not inside the project folder - the repo is not cloned yet'
}
Add-Line ''

# ---------------------------------------------------------------------
Add-Line '====================================================================='
Add-Line ' SUMMARY - what is missing for the desktop app plan'
Add-Line '====================================================================='
Add-Line ' Run the app     : Python + Pillow/numpy'
Add-Line ' Build frontend  : Node.js + npm'
Add-Line ' Build API/exe   : Go'
Add-Line ' Build Tauri shell: rustc + cargo + MSVC C++ + WebView2'
Add-Line ''
Add-Line ' Send this whole output back so the install steps can be tailored.'
Add-Line '====================================================================='

# Persist next to the script so it can be attached instead of copied.
$reportPath = Join-Path $PSScriptRoot 'desktop-env-report.txt'
if (-not $PSScriptRoot) { $reportPath = 'desktop-env-report.txt' }
$script:Report | Out-File -FilePath $reportPath -Encoding UTF8
Write-Host ''
Write-Host ("Report also saved to: {0}" -f $reportPath)