<#
Builds the Ridgeline Windows installer (build\installer\Ridgeline-Setup-<version>.exe).

    .\installer\build.ps1 -Python C:\Path\To\Python312 -Version 0.1.0

-Python   folder of a Python 3.12 install to bundle (a full Windows install is
          relocatable). Default: the folder of `py -3.12`.
-Version  version shown in the installer (default: pyproject.toml's).

Needs Inno Setup 6 (iscc.exe on PATH or in its default folder). GitHub's
Windows runners have it; the release workflow runs this script.
#>
param(
    [string]$Python,
    [string]$Version
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$build = Join-Path $root "build"
$stage = Join-Path $build "stage"

if (-not $Version) {
    $Version = (Select-String -Path (Join-Path $root "pyproject.toml") -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
}
if (-not $Python) {
    $Python = Split-Path (& py -3.12 -c "import sys; print(sys.executable)")
}
Write-Host "Building Ridgeline $Version with Python from $Python"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage | Out-Null

# 1. Private Python with every dependency and FreqUI.
Copy-Item -Recurse (Join-Path $Python "*") (Join-Path $stage "python")
$py = Join-Path $stage "python\python.exe"
& $py -m pip install --disable-pip-version-check --no-warn-script-location -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE) { throw "pip install failed" }
& $py -m freqtrade install-ui
if ($LASTEXITCODE) { throw "freqtrade install-ui failed" }
# Caches and tests don't need to ship.
Get-ChildItem (Join-Path $stage "python") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 2. The app. user_data ships only its tracked defaults: config, strategies,
#    hyperopt loss, research results. Never private config, data or databases.
foreach ($dir in "panel", "ridgeline", "scripts", "docs") {
    Copy-Item -Recurse (Join-Path $root $dir) (Join-Path $stage $dir)
}
foreach ($file in "README.md", "RESEARCH.md", "Crypto Trading Bot Case Study.pdf") {
    Copy-Item (Join-Path $root $file) $stage
}
$ud = Join-Path $stage "user_data"
New-Item -ItemType Directory -Force (Join-Path $ud "research"), (Join-Path $ud "hyperopts") | Out-Null
Copy-Item (Join-Path $root "user_data\config.json") $ud
Copy-Item -Recurse (Join-Path $root "user_data\strategies") $ud
Copy-Item (Join-Path $root "user_data\hyperopts\*.py") (Join-Path $ud "hyperopts")
Copy-Item (Join-Path $root "user_data\research\*.json") (Join-Path $ud "research")
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot "Ridgeline.cmd"), (Join-Path $PSScriptRoot "Update market data.cmd"), (Join-Path $PSScriptRoot "INSTALL-NOTES.txt") $stage

# 3. Compile.
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 not found (iscc.exe)" }
& $iscc "/DAppVersion=$Version" "/DStageDir=$stage" "/DOutputDir=$(Join-Path $build 'installer')" (Join-Path $PSScriptRoot "ridgeline.iss")
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem (Join-Path $build "installer") -Filter "*.exe" | ForEach-Object { Write-Host ("{0}  {1:N0} MB" -f $_.FullName, ($_.Length / 1MB)) }
