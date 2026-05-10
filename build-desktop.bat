@echo off
setlocal
cd /d "%~dp0"

if "%PYTHON_BIN%"=="" (
  set "PYTHON_BIN=python"
)

"%PYTHON_BIN%" -m pip install -e ".[desktop]"
if errorlevel 1 exit /b 1

"%PYTHON_BIN%" -m PyInstaller --noconfirm packaging/responses-proxy.spec
if errorlevel 1 exit /b 1

echo.
echo Desktop app build completed.
echo Output directory: %CD%\dist
