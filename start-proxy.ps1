$argsList = @(".\scripts\run_proxy.py", "--config", ".\model-config.json")
if ($args.Count -gt 0) {
    $argsList += $args
}

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $scriptDir "..\monitoring-platform\backend\.venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    $python = "python"
}

Set-Location $scriptDir
Write-Host "Starting raw proxy directly."
Write-Host "For the full visual manager, use start-manager.ps1."
Write-Host ""
& $python @argsList
exit $LASTEXITCODE
