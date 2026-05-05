@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0..\monitoring-platform\backend\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" ".\scripts\run_manager.py" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo Manager failed to start.
    pause
)
exit /b %EXITCODE%
