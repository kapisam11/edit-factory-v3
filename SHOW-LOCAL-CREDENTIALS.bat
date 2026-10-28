@echo off
setlocal
cd /d "%~dp0"

start "" powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0SHOW-LOCAL-CREDENTIALS.ps1"
exit /b 0
