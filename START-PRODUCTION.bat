@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0START-PRODUCTION.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Edit Factory v3 production startup finished successfully.
) else (
    echo Edit Factory v3 production startup failed. See the output above.
)
echo.
pause
exit /b %EXIT_CODE%
