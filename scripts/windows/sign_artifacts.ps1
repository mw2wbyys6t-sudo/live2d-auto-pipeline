# Authenticode signing for desktop build artifacts.
#
# Provider agnostic:
#   -SignCommand   any signing CLI; "{file}" is replaced with the quoted path
#                  (works with signtool wrappers, SSL.com CodeSignTool,
#                   DigiCert smctl, Azure artifact signing CLIs, ...)
#   -PfxPath       signtool with a .pfx file
#   -PfxBase64     same, but the .pfx arrives base64 encoded (CI secret)
#   -Thumbprint    signtool with a certificate from the local store
#
# Mode:
#   off       do nothing (default) - artifacts stay unsigned
#   optional  sign when a signer is configured, otherwise warn and continue
#   required  fail when no signer is configured or verification does not pass
#
# Exit codes: 0 = signed or intentionally skipped, 1 = signing/verification failed.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Path,

    [ValidateSet('off', 'optional', 'required')]
    [string]$Mode = 'off',

    [string]$SignCommand = '',
    [string]$PfxPath = '',
    [string]$PfxBase64 = '',
    [string]$PfxPassword = '',
    [string]$Thumbprint = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0

function Write-Info([string]$Message) { Write-Host "==> $Message" }
function Write-Warn2([string]$Message) { Write-Host "    [WARN] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "    [FAIL] $Message" -ForegroundColor Red }

if ($Mode -eq 'off') {
    Write-Info 'Signing skipped (Mode=off). Artifacts stay unsigned.'
    exit 0
}

# ---------------------------------------------------------------- artifacts
$files = @()
foreach ($pattern in $Path) {
    $matched = @(Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue)
    if ($matched.Count -eq 0) {
        Write-Warn2 "no file matched: $pattern"
        continue
    }
    $files += $matched
}

if ($files.Count -eq 0) {
    if ($Mode -eq 'required') {
        Write-Fail 'Mode=required but no artifact matched the given paths.'
        exit 1
    }
    Write-Warn2 'no artifact matched; nothing to sign.'
    exit 0
}

# ------------------------------------------------------------ key material
$tempPfx = ''
if (($PfxPath -eq '') -and ($PfxBase64 -ne '')) {
    $tempPfx = Join-Path ([System.IO.Path]::GetTempPath()) ('l2d-sign-' + [Guid]::NewGuid().ToString('N') + '.pfx')
    [System.IO.File]::WriteAllBytes($tempPfx, [Convert]::FromBase64String($PfxBase64))
    $PfxPath = $tempPfx
    Write-Info "decoded pfx to a temporary file ($(Split-Path -Leaf $tempPfx))"
}

$signer = 'none'
if ($SignCommand -ne '') { $signer = 'command' }
elseif ($PfxPath -ne '') { $signer = 'pfx' }
elseif ($Thumbprint -ne '') { $signer = 'thumbprint' }

if ($signer -eq 'none') {
    if ($Mode -eq 'required') {
        Write-Fail 'Mode=required but no signer is configured.'
        Write-Host '    Provide one of: -SignCommand, -PfxPath/-PfxBase64, -Thumbprint.'
        exit 1
    }
    Write-Warn2 'no signer configured; artifacts stay unsigned.'
    exit 0
}

# --------------------------------------------------------------- signtool
$signTool = ''
if ($signer -ne 'command') {
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $onPath) { $signTool = $onPath.Source }

    if ($signTool -eq '') {
        $roots = @()
        if ($env:ProgramFiles) { $roots += (Join-Path $env:ProgramFiles 'Windows Kits\10\bin') }
        $x86 = [System.Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
        if ($x86) { $roots += (Join-Path $x86 'Windows Kits\10\bin') }

        foreach ($root in $roots) {
            if (-not (Test-Path $root)) { continue }
            $found = Get-ChildItem -Path $root -Recurse -Filter 'signtool.exe' -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -First 1
            if ($null -ne $found) {
                $signTool = $found.FullName
                break
            }
        }
    }

    if ($signTool -eq '') {
        if ($Mode -eq 'required') {
            Write-Fail 'signtool.exe not found on PATH nor under Windows Kits\10\bin.'
            exit 1
        }
        Write-Warn2 'signtool.exe not found; artifacts stay unsigned.'
        exit 0
    }
    Write-Info "signtool: $signTool"
}

$canVerify = $null -ne (Get-Command Get-AuthenticodeSignature -ErrorAction SilentlyContinue)
if (-not $canVerify) {
    Write-Warn2 'Get-AuthenticodeSignature is unavailable on this host; verification will be skipped.'
}

# ------------------------------------------------------------------- sign
$results = @()
$failures = 0

foreach ($file in $files) {
    $entry = [pscustomobject]@{
        File   = $file.Name
        Signed = $false
        Status = ''
        Signer = ''
    }

    try {
        $global:LASTEXITCODE = 0

        switch ($signer) {
            'command' {
                $quoted = '"' + $file.FullName + '"'
                $line = $SignCommand.Replace('{file}', $quoted)
                Write-Info "signing $($file.Name) with the custom sign command"
                Invoke-Expression $line
                if (($null -ne $LASTEXITCODE) -and ($LASTEXITCODE -ne 0)) {
                    throw "custom sign command exited with code $LASTEXITCODE"
                }
            }
            'pfx' {
                Write-Info "signing $($file.Name) with signtool (pfx)"
                & $signTool sign /fd sha256 /f $PfxPath /p $PfxPassword /tr $TimestampUrl /td sha256 $file.FullName
                if ($LASTEXITCODE -ne 0) { throw "signtool exited with code $LASTEXITCODE" }
            }
            'thumbprint' {
                Write-Info "signing $($file.Name) with signtool (certificate store)"
                & $signTool sign /fd sha256 /sha1 $Thumbprint /tr $TimestampUrl /td sha256 $file.FullName
                if ($LASTEXITCODE -ne 0) { throw "signtool exited with code $LASTEXITCODE" }
            }
        }

        if ($canVerify) {
            $sig = Get-AuthenticodeSignature -FilePath $file.FullName
            $entry.Status = [string]$sig.Status
            if ($null -eq $sig.SignerCertificate) {
                throw 'no Authenticode signature found after signing'
            }
            $entry.Signer = $sig.SignerCertificate.Subject
            $entry.Signed = $true
            if (($Mode -eq 'required') -and ($sig.Status -ne 'Valid')) {
                throw "signature status is '$($sig.Status)' (expected 'Valid')"
            }
        }
        else {
            $entry.Signed = $true
            $entry.Status = 'unverified'
        }
    }
    catch {
        Write-Fail "$($file.Name): $($_.Exception.Message)"
        $entry.Status = 'error: ' + $_.Exception.Message
        $failures++
    }

    $results += $entry
}

# ---------------------------------------------------------------- summary
Write-Info 'signing summary'
$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host

if (($tempPfx -ne '') -and (Test-Path $tempPfx)) {
    Remove-Item $tempPfx -Force -ErrorAction SilentlyContinue
    Write-Info 'temporary pfx removed'
}

if ($failures -gt 0) {
    Write-Fail "$failures artifact(s) failed signing or verification"
    exit 1
}

Write-Info 'all requested artifacts are signed'
exit 0
