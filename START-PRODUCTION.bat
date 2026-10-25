@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0START-PRODUCTION.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Edit Factory v3 production startup failed. The error details are above.
    echo This window will stay open so you can read the failure.
    echo.
    pause
)
exit /b %EXIT_CODE%
