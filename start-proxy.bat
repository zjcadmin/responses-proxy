@echo off
setlocal
cd /d "%~dp0"

REM Python resolver: scripts\resolve-python.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-proxy.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo Proxy failed to start.
    echo If the configured port is already occupied, run stop-proxy.bat first.
    pause
)
exit /b %EXITCODE%
