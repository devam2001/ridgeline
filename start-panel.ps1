# Starts the control panel at http://127.0.0.1:8090 (paper trading, replay, research).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "Panel: http://127.0.0.1:8090   (Ctrl+C to stop)"
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn panel.app:app --host 127.0.0.1 --port 8090
