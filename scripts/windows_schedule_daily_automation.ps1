# Windows Scheduled Task Install for the Daily Improvement loop (2026-09-08)
# Run in PowerShell as Administrator.
#
# Registers 'ApexTraderDailyImprovement': a repeated 15-min cadence through
# the LOCAL midday (11:00-14:30) on weekdays. daily_automation.py itself is
# the authority on the ET window (12:05-14:00 ET, pytz) and on the market-day
# check, so DST drift / clock skew cannot push the work into an active
# trading window. MultipleInstances=IgnoreNew + the machine-local lock file
# prevent overlapping runs.
#
# SAFETY: the task runs WITHOUT AUTOMATION_ALLOW_DEPLOY set, so runs are
# observe/plan-only until you explicitly opt in to the test-gated deploy
# (set AUTOMATION_ALLOW_DEPLOY=1 machine-wide, or pass --allow-deploy).

$taskName = 'ApexTraderDailyImprovement'
$taskDescription = 'ApexTrader daily observation + evidence-gated improvement loop (ET window enforced inside the script).'
$BaseDir = $PSScriptRoot

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BaseDir\scripts\run_daily_automation_task.ps1`""

$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 11:00
$repeat = New-ScheduledTaskTrigger -Once -At 11:00 `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Hours 3.5)
$weekly.Repetition = $repeat.Repetition

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:UserName" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$task = New-ScheduledTask -Action $action -Trigger $weekly -Principal $principal `
    -Settings $settings -Description $taskDescription

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    throw "Not running as Administrator. Right-click PowerShell and 'Run as administrator', then re-run this script."
}

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force -ErrorAction Stop
Write-Host "Scheduled task '$taskName' installed successfully."
Write-Host "Runs (15-min cadence, local 11:00-14:30 weekdays); script enforces 12:05-14:00 ET + market day."
Write-Host "Manual one-shot run:  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Manual foreground run: powershell -File $BaseDir\scripts\run_daily_automation_task.ps1 -Force -Offline"
Write-Host "Deploy opt-in (test-gated): set AUTOMATION_ALLOW_DEPLOY=1 (machine env) before enabling."
