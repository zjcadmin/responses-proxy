@echo off
setlocal
cd /d "%~dp0"

REM Python resolver: scripts\resolve-python.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-manager.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo Manager failed to start.
    pause
)
exit /b %EXITCODE%
