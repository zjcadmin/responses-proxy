@echo off
setlocal
cd /d "%~dp0"

echo Starting raw proxy directly.
echo For the full visual manager, use start-manager.bat.
echo.

set "PYTHON=%~dp0..\monitoring-platform\backend\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" ".\scripts\run_proxy.py" --config ".\model-config.json" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo Proxy failed to start.
    echo If the configured port is already occupied, run stop-proxy.bat first.
    pause
)
exit /b %EXITCODE%
