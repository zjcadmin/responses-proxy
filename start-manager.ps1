$argsList = @(".\scripts\run_manager.py")
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
& $python @argsList
exit $LASTEXITCODE
