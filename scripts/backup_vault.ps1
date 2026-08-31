[CmdletBinding(SupportsShouldProcess, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
$resolvedSource = (Resolve-Path -LiteralPath $Source).Path
$repo = $env:RESTIC_REPOSITORY

if (-not $repo) {
    throw "RESTIC_REPOSITORY is not configured."
}
if (-not $env:RESTIC_PASSWORD -and -not $env:RESTIC_PASSWORD_FILE) {
    throw "Configure RESTIC_PASSWORD or RESTIC_PASSWORD_FILE outside the repository."
}
if (-not (Get-Command restic -ErrorAction SilentlyContinue)) {
    throw "restic is not installed or not on PATH."
}
Write-Host "Source: $resolvedSource"
Write-Host "Repository: $repo"
if (-not $Execute) {
    Write-Host "Dry run only. Add -Execute to request a real snapshot."
    & restic backup $resolvedSource --dry-run
    exit $LASTEXITCODE
}

if ($PSCmdlet.ShouldProcess($resolvedSource, "Create an encrypted restic snapshot")) {
    & restic backup $resolvedSource
    exit $LASTEXITCODE
}
