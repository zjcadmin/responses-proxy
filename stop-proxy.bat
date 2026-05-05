@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0..\monitoring-platform\backend\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" ".\scripts\stop_proxy.py" --config ".\model-config.json" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo Proxy failed to stop.
    pause
)
exit /b %EXITCODE%
