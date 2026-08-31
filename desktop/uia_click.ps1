[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WindowTitleBase64,
    [Parameter(Mandatory = $true)][string]$ControlNameBase64,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$AutomationIdBase64,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$ControlTypeBase64
)

$ErrorActionPreference = "Stop"

function Decode-Selector([string]$Value) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value))
}

$windowTitle = Decode-Selector $WindowTitleBase64
$controlName = Decode-Selector $ControlNameBase64
$automationId = Decode-Selector $AutomationIdBase64
$controlType = Decode-Selector $ControlTypeBase64

Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName UIAutomationClient

$windowNameCondition = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::NameProperty,
    $windowTitle
)
$windowTypeCondition = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::ControlTypeProperty,
    [Windows.Automation.ControlType]::Window
)
$windowCondition = New-Object Windows.Automation.AndCondition(
    $windowNameCondition,
    $windowTypeCondition
)
$windows = [Windows.Automation.AutomationElement]::RootElement.FindAll(
    [Windows.Automation.TreeScope]::Children,
    $windowCondition
)
if ($windows.Count -ne 1) {
    throw "The exact window title did not resolve to one visible application."
}

$nameCondition = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::NameProperty,
    $controlName
)
$candidates = $windows.Item(0).FindAll(
    [Windows.Automation.TreeScope]::Descendants,
    $nameCondition
)
$matches = @()
for ($index = 0; $index -lt $candidates.Count; $index += 1) {
    $candidate = $candidates.Item($index)
    $current = $candidate.Current
    $currentType = $current.ControlType.ProgrammaticName.Replace("ControlType.", "")
    if ($automationId -and $current.AutomationId -ne $automationId) {
        continue
    }
    if ($controlType -and $currentType -ne $controlType) {
        continue
    }
    $matches += $candidate
}
if ($matches.Count -ne 1) {
    throw "The exact control name did not resolve to one target in the selected window."
}

$control = $matches[0]
if (-not $control.Current.IsEnabled -or $control.Current.IsOffscreen) {
    throw "The exact control is not currently visible and enabled."
}
$invoke = $control.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
([Windows.Automation.InvokePattern]$invoke).Invoke()

[PSCustomObject]@{
    window_title = $windowTitle
    control_name = $controlName
    automation_id = if ($automationId) { $automationId } else { $null }
    control_type = $control.Current.ControlType.ProgrammaticName.Replace("ControlType.", "")
    clicked = $true
} | ConvertTo-Json -Compress
