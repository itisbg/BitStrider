# Windows Scheduled Task Install for the TI Capture scraper
# Run in PowerShell as Administrator.

$taskName = 'ApexTraderTICapture'
$taskDescription = 'Keep data/ti_primary.json fresh by looping the Trade Ideas scraper, starting at log on'
$BaseDir = $PSScriptRoot

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BaseDir\scripts\run_ti_capture_task.ps1`""
# Starts when you log on, since the scraper needs your real, already-logged-in Edge profile/session.
$trigger = New-ScheduledTaskTrigger -AtLogOn

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:UserName" -LogonType Interactive -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $taskDescription

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    throw "Not running as Administrator. Right-click PowerShell and 'Run as administrator', then re-run this script."
}

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force -ErrorAction Stop
Write-Host "Scheduled task '$taskName' installed successfully. Use 'schtasks /query /tn $taskName' to inspect."
