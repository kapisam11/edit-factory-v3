@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0START-ALL.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Edit Factory v3 START-ALL finished successfully.
) else (
    echo Edit Factory v3 START-ALL finished with failures. See the output above.
)
echo.
pause
exit /b %EXIT_CODE%