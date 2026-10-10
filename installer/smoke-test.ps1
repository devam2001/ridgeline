<#
Installs a built Ridgeline installer silently into a temporary folder and
checks that it works: private config created, FreqUI bundled, strategies
load, the panel starts and answers, and uninstalling keeps user_data.

    .\installer\smoke-test.ps1 -Installer build\installer\Ridgeline-Setup-0.1.0.exe

The release workflow runs this on every build.
#>
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = "Stop"
$dir = Join-Path ([IO.Path]::GetTempPath()) "ridgeline-smoke"
if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }

$setup = Start-Process -Wait -PassThru (Resolve-Path $Installer) -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=", "/DIR=`"$dir`""
if ($setup.ExitCode) { throw "installer exited with $($setup.ExitCode)" }

$py = Join-Path $dir "python\python.exe"
foreach ($path in "user_data\config.private.json", "user_data\config.json", "Ridgeline.cmd",
                  "python\Lib\site-packages\freqtrade\rpc\api_server\ui\installed\index.html") {
    if (-not (Test-Path (Join-Path $dir $path))) { throw "missing after install: $path" }
}

Push-Location $dir
try {
    & $py -m freqtrade list-strategies --userdir user_data -c user_data\config.json
    if ($LASTEXITCODE) { throw "strategies failed to load" }

    $panel = Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m", "uvicorn", "panel.app:app", "--host", "127.0.0.1", "--port", "8090"
    try {
        $strategies = $null
        for ($i = 0; $i -lt 45 -and -not $strategies; $i++) {
            Start-Sleep -Seconds 2
            try { $strategies = Invoke-RestMethod "http://127.0.0.1:8090/api/strategies" } catch { }
        }
        if ($strategies -notcontains "TrendRider4h") { throw "panel did not list the strategies: $strategies" }
        $mode = Invoke-RestMethod "http://127.0.0.1:8090/api/mode"
        Write-Host "panel ok, version $($mode.version): $($strategies -join ', ')"
    }
    finally {
        Stop-Process -Id $panel.Id -Force -ErrorAction SilentlyContinue
    }
}
finally {
    Pop-Location
}

$uninstall = Start-Process -Wait -PassThru (Join-Path $dir "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
if ($uninstall.ExitCode) { throw "uninstaller exited with $($uninstall.ExitCode)" }
Start-Sleep -Seconds 3  # the uninstaller finishes in a helper process
if (Test-Path (Join-Path $dir "python")) { throw "uninstall left the Python environment behind" }
if (-not (Test-Path (Join-Path $dir "user_data\config.private.json"))) { throw "uninstall removed the private config" }
Write-Host "install, panel and uninstall ok"
