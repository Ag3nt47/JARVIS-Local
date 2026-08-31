[CmdletBinding()]
param(
    [string]$DestinationDirectory = "D:\J.A.R.V.I.S\App"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$sourceDirectory = Join-Path $repoRoot "dist\JARVIS"
$sourceExecutable = Join-Path $sourceDirectory "JARVIS.exe"

if (-not (Test-Path -LiteralPath $sourceExecutable -PathType Leaf)) {
    throw "The packaged app was not found. Run scripts\build_desktop.ps1 first."
}

$resolvedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $DestinationDirectory))
$resolvedDestination = [System.IO.Path]::GetFullPath($DestinationDirectory)
if ($resolvedDestination -eq [System.IO.Path]::GetPathRoot($resolvedDestination)) {
    throw "The installation destination cannot be a drive root."
}
if (-not $resolvedDestination.StartsWith($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The installation destination is invalid."
}

New-Item -ItemType Directory -Path $resolvedDestination -Force | Out-Null
Get-ChildItem -LiteralPath $sourceDirectory -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $resolvedDestination -Recurse -Force
}

$installedExecutable = Join-Path $resolvedDestination "JARVIS.exe"
if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
    throw "The app copy completed without producing the installed executable."
}

$desktopDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $desktopDirectory) {
    throw "Windows did not report a Desktop directory for the current user."
}
$shortcutPath = Join-Path $desktopDirectory "JARVIS.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $installedExecutable
$shortcut.WorkingDirectory = $resolvedDestination
$shortcut.IconLocation = "$installedExecutable,0"
$shortcut.Description = "Launch the JARVIS local desktop assistant"
$shortcut.Save()

Write-Host "JARVIS installed: $installedExecutable"
Write-Host "Desktop shortcut: $shortcutPath"
