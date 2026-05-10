$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

& $Python -m pip install -e ".[desktop]"
& $Python -m PyInstaller --noconfirm packaging/responses-proxy.spec

Write-Host ""
Write-Host "Desktop app build completed."
Write-Host "Output directory: $ProjectRoot\dist"
