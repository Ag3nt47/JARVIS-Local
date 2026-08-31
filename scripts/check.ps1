[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Jarvis is not bootstrapped. Run scripts\bootstrap.ps1 -WithDevTools first."
}
Push-Location $repoRoot
try {
    & $venvPython -m ruff check src tests
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff checks failed (exit code $LASTEXITCODE)."
    }
    & $venvPython -m pytest
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed (exit code $LASTEXITCODE)."
    }
} finally {
    Pop-Location
}
