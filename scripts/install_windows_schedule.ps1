param(
    [string]$TaskName = "FundOS Daily Production",
    [string]$At = "18:30",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$python = Join-Path $ProjectRoot ".venv\bin\python.exe"
$script = Join-Path $ProjectRoot "scripts\run_production.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python was not found: $python"
}
if (-not (Test-Path -LiteralPath $script)) {
    throw "Production script was not found: $script"
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "`"$script`"" `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Run the FundOS production data and operations pipeline." `
    -Force

Write-Host "Scheduled task '$TaskName' installed for $At."
