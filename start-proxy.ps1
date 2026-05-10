$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "scripts\resolve-python.ps1")
$python = Resolve-Python $scriptDir

$argsList = @(".\scripts\run_proxy.py", "--config", ".\model-config.json")
if ($args.Count -gt 0) {
    $argsList += $args
}

Set-Location $scriptDir
Write-Host "Starting raw proxy directly."
Write-Host "For the full visual manager, use start-manager.ps1."
Write-Host ""
& $python @argsList
exit $LASTEXITCODE
