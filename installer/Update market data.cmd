@echo off
rem Downloads Binance candles and refreshes the live pair list (README, "Data").
rem The first run downloads several GB and can take a while; later runs only top up.
rem Run it monthly, then restart any paper session to pick up the new pair list.
title Ridgeline - update market data
cd /d "%~dp0"
if not exist "user_data\config.private.json" "python\python.exe" scripts\init_private_config.py
"python\python.exe" scripts\update_data.py
pause
