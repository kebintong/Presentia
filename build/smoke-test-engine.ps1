<#
  smoke-test-engine.ps1 - start the frozen engine (PyInstaller build) and make
  sure it really works, the way the installed app will run it.

  The usual way a release breaks is a module PyInstaller left out: it works
  in development and fails with ModuleNotFoundError only once installed. This
  starts dist\presentia-sidecar\presentia-sidecar.exe and calls endpoints that
  load the lazily imported parts (Excel export, website/diagnostics, HTTPS
  certificate libraries, Windows Graphics Capture, ONNX Runtime). The face
  models are not downloaded.

  Usage (from the project root, after pyinstaller):
      powershell -ExecutionPolicy Bypass -File build\smoke-test-engine.ps1
#>

[CmdletBinding()]
param(
    [string]$Exe = 'dist\presentia-sidecar\presentia-sidecar.exe',
    [int]$Port = 7799,
    [int]$StartupSeconds = 120
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not [System.IO.Path]::IsPathRooted($Exe)) { $Exe = Join-Path $root $Exe }
if (-not (Test-Path $Exe)) { throw "Frozen engine not found: $Exe (run pyinstaller first)" }

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("presentia-smoke-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$outLog = Join-Path $work 'stdout.log'
$errLog = Join-Path $work 'stderr.log'

# A throwaway data folder, and no face-model download.
$env:PRESENTIA_DATA_DIR = Join-Path $work 'data'
$env:PRESENTIA_SKIP_ENGINE = '1'
$base = "http://127.0.0.1:$Port"

function Call([string]$Method, [string]$Path, $Body = $null) {
    $req = @{ Uri = "$base$Path"; Method = $Method; UseBasicParsing = $true; TimeoutSec = 60 }
    if ($null -ne $Body) {
        $req.Body = ($Body | ConvertTo-Json -Compress)
        $req.ContentType = 'application/json'
    }
    return Invoke-WebRequest @req
}

function Check([string]$Name, [scriptblock]$Test) {
    try {
        $detail = & $Test
        Write-Host ("  OK    {0}{1}" -f $Name, $(if ($detail) { " ($detail)" } else { '' }))
    } catch {
        Write-Host ("  FAIL  {0}: {1}" -f $Name, $_.Exception.Message) -ForegroundColor Red
        $script:failed = $true
    }
}

Write-Host "Starting $Exe on port $Port"
$proc = Start-Process -FilePath $Exe -ArgumentList '--host', '127.0.0.1', '--port', "$Port" `
    -WorkingDirectory (Split-Path $Exe) -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
$failed = $false

try {
    # Wait until it answers (first start of a onedir build can be slow).
    $deadline = (Get-Date).AddSeconds($StartupSeconds)
    $up = $false
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { throw "The engine exited during start-up (exit code $($proc.ExitCode))." }
        try { Call GET '/api/engine/status' | Out-Null; $up = $true; break } catch { Start-Sleep -Milliseconds 700 }
    }
    if (-not $up) { throw "The engine did not answer within $StartupSeconds seconds." }

    Check 'engine status' { (Call GET '/api/engine/status').StatusCode }
    Check 'performance settings (ONNX Runtime)' {
        $perf = (Call GET '/api/perf').Content | ConvertFrom-Json
        if ($null -eq $perf) { throw 'empty answer' }
        'answered'
    }
    $cls = $null
    Check 'create a class (database)' {
        $script:cls = (Call POST '/api/classes' @{ name = 'Smoke test'; section = 'CI' }).Content | ConvertFrom-Json
        "id $($script:cls.id)"
    }
    Check 'Excel export' {
        $res = Call GET "/api/classes/$($script:cls.id)/export.xlsx"
        $bytes = $res.RawContentStream.ToArray()
        if ($bytes.Length -lt 4 -or $bytes[0] -ne 0x50 -or $bytes[1] -ne 0x4B) { throw 'not an .xlsx (zip) file' }
        "$($bytes.Length) bytes"
    }
    Check 'diagnostics (certificates, window capture, diagnostic mode)' {
        $d = (Call GET '/api/diagnostics?network=false').Content | ConvertFrom-Json
        $missing = @($d.system | Where-Object { $_.value -eq 'missing' } | ForEach-Object { $_.label })
        if ($missing.Count) { throw "missing in the build: $($missing -join ', ')" }
        $wgc = ($d.system | Where-Object { $_.label -eq 'Window capture' }).value
        "truststore and certifi present; $wgc"
    }
    Check 'website settings' { ((Call GET '/api/cloud').Content | ConvertFrom-Json).url }
    Check 'data summary' { "$(((Call GET '/api/data/summary').Content | ConvertFrom-Json).classes) class" }
    Check 'still running' { if ($proc.HasExited) { throw "exited with code $($proc.ExitCode)" } }
} catch {
    Write-Host "  FAIL  $($_.Exception.Message)" -ForegroundColor Red
    $failed = $true
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

if ($failed) {
    Write-Host "`n--- engine stderr ---"
    if (Test-Path $errLog) { Get-Content $errLog -Tail 80 }
    Write-Host "--- engine stdout ---"
    if (Test-Path $outLog) { Get-Content $outLog -Tail 40 }
    Write-Host "`nThe frozen engine is broken. If the log shows ModuleNotFoundError, add the module to"
    Write-Host "hiddenimports in build\presentia-sidecar.spec."
    exit 1
}
Write-Host "`nThe frozen engine works."
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
