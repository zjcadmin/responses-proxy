$ErrorActionPreference = "Stop"

function Test-ProjectPython {
    param([string] $PythonCommand)

    try {
        & $PythonCommand -c "import fastapi, uvicorn, pydantic, httpx" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-Python {
    param([string] $ProjectRoot)

    $candidates = @()
    if ($env:RESPONSES_PROXY_PYTHON) {
        $candidates += $env:RESPONSES_PROXY_PYTHON
    }
    $candidates += Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $candidates += Join-Path $ProjectRoot "venv\Scripts\python.exe"
    $candidates += Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    $candidates += "python"

    foreach ($candidate in $candidates) {
        if ($candidate -match "\\python\.exe$" -and -not (Test-Path $candidate)) {
            continue
        }
        if (Test-ProjectPython $candidate) {
            return $candidate
        }
    }

    throw "No usable Python found. Install dependencies with: python -m pip install -e ."
}
