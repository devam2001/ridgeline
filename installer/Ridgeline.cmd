@echo off
rem Starts the Ridgeline panel at http://127.0.0.1:8090 and opens it in your browser.
rem Close this window (or press Ctrl+C) to stop the panel. A paper-trading bot
rem started from the panel keeps running; stop it from the panel first.
title Ridgeline panel
cd /d "%~dp0"
if not exist "user_data\config.private.json" "python\python.exe" scripts\init_private_config.py
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start "" http://127.0.0.1:8090"
echo Ridgeline panel: http://127.0.0.1:8090   (close this window to stop it)
"python\python.exe" -m uvicorn panel.app:app --host 127.0.0.1 --port 8090
if errorlevel 1 pause
