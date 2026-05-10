$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "scripts\resolve-python.ps1")
$python = Resolve-Python $scriptDir

$argsList = @(".\scripts\stop_manager.py", "--config", ".\manager-config.json")
if ($args.Count -gt 0) {
    $argsList += $args
}

Set-Location $scriptDir
& $python @argsList
exit $LASTEXITCODE
