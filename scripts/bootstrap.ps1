[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$WithDevTools
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if ($Python) {
    $pythonCommand = (Get-Command $Python -ErrorAction SilentlyContinue).Source
    $pythonPrefix = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = (Get-Command py).Source
    $pythonPrefix = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = (Get-Command python).Source
    $pythonPrefix = @()
} else {
    $pythonCommand = $null
    $pythonPrefix = @()
}

if (-not $pythonCommand) {
    throw "Python 3.11 or newer was not found. Install Python, then rerun this script."
}

$pythonVersion = & $pythonCommand @pythonPrefix -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python command could not start. Pass -Python with a working Python 3.11+ executable."
}
$versionParts = $pythonVersion.Trim().Split(".")
if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 11)) {
    throw "Jarvis requires Python 3.11 or newer; found $pythonVersion."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $pythonCommand @pythonPrefix -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Jarvis virtual environment (exit code $LASTEXITCODE)."
    }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip (exit code $LASTEXITCODE)."
}
if ($WithDevTools) {
    & $venvPython -m pip install -e "${repoRoot}[dev]"
} else {
    & $venvPython -m pip install -e $repoRoot
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Jarvis (exit code $LASTEXITCODE)."
}

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    Write-Host "Ollama detected: $($ollama.Source)"
    & ollama list
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Ollama is installed but did not answer the model-list request."
    }
} else {
    Write-Warning "Ollama was not found on PATH. Install or start Ollama before using chat or memory."
}

Write-Host "Jarvis environment ready. Run scripts\run.ps1 from the repository."
