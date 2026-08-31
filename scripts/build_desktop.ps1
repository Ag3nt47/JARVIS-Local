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
    & $venvPython -m pip install -e "${repoRoot}[desktop]"
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop dependencies failed to install (exit code $LASTEXITCODE)."
    }
    & $venvPython -m PyInstaller --noconfirm --clean desktop/JARVIS.spec
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop packaging failed (exit code $LASTEXITCODE)."
    }
    $executable = Join-Path $repoRoot "dist\JARVIS\JARVIS.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Desktop packaging completed without producing JARVIS.exe."
    }
    Write-Host "Desktop app ready: $executable"
} finally {
    Pop-Location
}
