[CmdletBinding()]
param(
    [string]$Config = "config/default.toml"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$configPath = if ([System.IO.Path]::IsPathRooted($Config)) {
    [System.IO.Path]::GetFullPath($Config)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Config))
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Jarvis is not bootstrapped. Run scripts\bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Jarvis config file was not found: $configPath"
}
$env:JARVIS_CONFIG = $configPath
Push-Location $repoRoot
try {
    & $venvPython -m jarvis
    if ($LASTEXITCODE -ne 0) {
        throw "Jarvis exited with code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
