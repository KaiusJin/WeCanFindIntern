param(
    [string]$TaskName = "WeCanFindIntern Collector",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Runner = Join-Path $ProjectDir "scripts\collection\run_collection_campaign_windows.ps1"
$TaskPath = "\WeCanFindIntern\"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskPath$TaskName"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours 4) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $action `
    -Trigger $trigger -Settings $settings `
    -Description "Collect and enrich WeCanFindIntern job postings every four hours." `
    -User $env:USERNAME -RunLevel Limited -Force | Out-Null

Write-Host "Registered scheduled task: $TaskPath$TaskName"
