<#
  set-version.ps1 — set Presentia's version number in every place it appears.

  The version lives in three files, and they MUST agree with the git tag you
  publish. If presentia-desktop/update.go says 1.0.0 but you tag the release
  v1.1.0, then every user who installs 1.1.0 still reports 1.0.0 and the
  update banner never goes away. This script keeps them in step.

  Usage (from the project root):
      powershell -ExecutionPolicy Bypass -File build\set-version.ps1 1.1.0
      powershell -ExecutionPolicy Bypass -File build\set-version.ps1 -Check

  -Check reports the current values without changing anything.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Version,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

$root      = Split-Path -Parent $PSScriptRoot
$updateGo  = Join-Path $root 'presentia-desktop\update.go'
$issFile   = Join-Path $root 'build\installer.iss'
$wailsJson = Join-Path $root 'presentia-desktop\wails.json'

foreach ($f in @($updateGo, $issFile, $wailsJson)) {
    if (-not (Test-Path $f)) { throw "Missing file: $f" }
}

$goPattern  = 'const\s+AppVersion\s*=\s*"([^"]*)"'
$issPattern = '#define\s+MyAppVersion\s+"([^"]*)"'

function Get-Current {
    $go  = [regex]::Match((Get-Content $updateGo  -Raw), $goPattern).Groups[1].Value
    $iss = [regex]::Match((Get-Content $issFile   -Raw), $issPattern).Groups[1].Value
    $wj  = (Get-Content $wailsJson -Raw | ConvertFrom-Json)
    $pv  = if ($wj.info) { $wj.info.productVersion } else { '' }
    [PSCustomObject]@{ UpdateGo = $go; Installer = $iss; WailsJson = $pv }
}

if ($Check -or -not $Version) {
    $cur = Get-Current
    Write-Host ''
    Write-Host '  update.go   AppVersion      : ' -NoNewline; Write-Host $cur.UpdateGo
    Write-Host '  installer.iss MyAppVersion  : ' -NoNewline; Write-Host $cur.Installer
    Write-Host '  wails.json  productVersion  : ' -NoNewline; Write-Host $cur.WailsJson
    Write-Host ''
    $all = @($cur.UpdateGo, $cur.Installer, $cur.WailsJson) | Select-Object -Unique
    if ($all.Count -eq 1 -and $all[0]) {
        Write-Host "  All in sync at $($all[0])." -ForegroundColor Green
        Write-Host "  Publish as tag: v$($all[0])"
    } else {
        Write-Host '  MISMATCH — run this script with a version to fix.' -ForegroundColor Red
    }
    Write-Host ''
    if (-not $Version) { return }
}

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must look like 1.2.3 (got '$Version')"
}

# update.go
$src = Get-Content $updateGo -Raw
if ($src -notmatch $goPattern) { throw "Could not find 'const AppVersion' in update.go" }
$src = [regex]::Replace($src, $goPattern, "const AppVersion = `"$Version`"")
Set-Content -Path $updateGo -Value $src -NoNewline -Encoding UTF8

# installer.iss
$src = Get-Content $issFile -Raw
if ($src -notmatch $issPattern) { throw "Could not find '#define MyAppVersion' in installer.iss" }
$src = [regex]::Replace($src, $issPattern, "#define MyAppVersion `"$Version`"")
Set-Content -Path $issFile -Value $src -NoNewline -Encoding UTF8

# wails.json — stamps the version into the exe's file properties
$wj = Get-Content $wailsJson -Raw | ConvertFrom-Json
if (-not $wj.info) {
    $wj | Add-Member -MemberType NoteProperty -Name info -Value ([PSCustomObject]@{})
}
$fields = @{
    companyName    = 'Presentia Team'
    productName    = 'Presentia'
    productVersion = $Version
    copyright      = 'Presentia Team'
    comments       = 'AI-Powered Virtual Classroom Attendance System'
}
foreach ($k in $fields.Keys) {
    if ($wj.info.PSObject.Properties.Name -contains $k) { $wj.info.$k = $fields[$k] }
    else { $wj.info | Add-Member -MemberType NoteProperty -Name $k -Value $fields[$k] }
}
$wj.info.productVersion = $Version
($wj | ConvertTo-Json -Depth 10) | Set-Content -Path $wailsJson -Encoding UTF8

Write-Host ''
Write-Host "  Version set to $Version in all three files." -ForegroundColor Green
Write-Host ''
Write-Host '  Next:'
Write-Host '    cd presentia-desktop'
Write-Host '    wails build'
Write-Host '    cd ..'
Write-Host '    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss'
Write-Host ''
Write-Host '  Then publish the release (tag MUST match):'
Write-Host "    git commit -am `"Release $Version`""
Write-Host "    git tag v$Version"
Write-Host '    git push --tags'
Write-Host "    Create a GitHub Release for tag v$Version and attach"
Write-Host '    build\output\PresentiaSetup.exe as an asset.'
Write-Host ''
