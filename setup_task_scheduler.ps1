# setup_task_scheduler.ps1
# Run this once in PowerShell (as yourself, no admin needed) to create
# the "Daily Outreach" scheduled task.
#
# How to run:
#   1. Right-click this file -> "Run with PowerShell"
#      OR open PowerShell and run:
#      powershell -ExecutionPolicy Bypass -File "C:\Users\abhin\OneDrive\Desktop\Connections\outreach-tool\setup_task_scheduler.ps1"

$batPath    = "C:\Users\abhin\OneDrive\Desktop\Connections\outreach-tool\run_daily.bat"
$taskName   = "Daily Outreach"

# Remove existing task with the same name if it exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Action: run cmd.exe /c "<bat file>"
$action = New-ScheduledTaskAction `
    -Execute  "cmd.exe" `
    -Argument "/c `"$batPath`""

# Trigger: every day at 10:00 AM
$trigger = New-ScheduledTaskTrigger -Daily -At "10:00AM"

# Settings: start when available, don't allow multiple instances
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Principal: run as current user, only when logged on (required for Playwright)
$principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERDOMAIN\$env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Limited

# Register
Register-ScheduledTask `
    -TaskName  $taskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force

Write-Host ""
Write-Host "Done! Task '$taskName' created." -ForegroundColor Green
Write-Host "It will run every day at 10:00 AM while you're logged in."
Write-Host ""
Write-Host "To verify: open Task Scheduler and look for '$taskName' in the task list."
Write-Host "To run it manually right now: Start-ScheduledTask -TaskName '$taskName'"