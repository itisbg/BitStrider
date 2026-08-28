# Windows Scheduled Task Install for the TI Capture scraper
# Run in PowerShell as Administrator.

$taskName = 'ApexTraderTICapture'
$taskDescription = 'Refresh Yahoo Finance data/ti_primary.json: every 3 min 09:09-10:30 ET, every 10 min 10:30-14:50 ET, every 3 min 14:50-15:50 ET, Mon-Fri — single-shot runs owned by Task Scheduler'
$BaseDir = $PSScriptRoot

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BaseDir\scripts\run_ti_capture_task.ps1`""

# The first Yahoo refresh must land at 09:09 ET (08:09 Central on this
# machine), not earlier. Dropped
# the old AtLogOn trigger: it fired an immediate scrape at whatever time the
# user happened to log into Windows, which could be well before 08:30 and made
# "first call" unpredictable. The weekly trigger below is Task-Scheduler-owned
# (not an internal Python loop), so it re-arms every cycle on its own even
# after a crashed run — no logon-trigger safety net needed for that anymore.
$repetitionClass = Get-CimClass -ClassName MSFT_TaskRepetitionPattern -Namespace Root/Microsoft/Windows/TaskScheduler

# Yahoo refresh: 09:09-10:30 ET = 08:09-09:30 Central, every 3 minutes.
$openingTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:09
# .Repetition isn't settable in place (returns a fresh, disconnected CIM instance
# each access) — build the repetition pattern separately and assign it whole.
$openingRepetition = New-CimInstance -CimClass $repetitionClass -ClientOnly
$openingRepetition.Interval = 'PT3M'
$openingRepetition.Duration = 'PT1H21M'  # 08:09 -> 09:30 (9:09-10:30am ET)
$openingRepetition.StopAtDurationEnd = $false
$openingTrigger.Repetition = $openingRepetition

# Rest of the session: every 10 minutes, 09:30 -> 13:50 Central
# (10:30am-2:50pm ET).
# entries fire after that, so fresh universe data past that point is
# pointless). 2026-08-22, user request: "even the webscrapping for universe
# should stop at 3.50ET" -- was PT5H30M out to 15:00 Central (market close,
# 4:00pm ET), which ran 10 min past the point entries actually stop.
$weekdayTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:30
$repetition = New-CimInstance -CimClass $repetitionClass -ClientOnly
$repetition.Interval = 'PT10M'
$repetition.Duration = 'PT4H20M'   # 09:30 -> 13:50 (10:30am-2:50pm ET)
$repetition.StopAtDurationEnd = $false
$weekdayTrigger.Repetition = $repetition

# Evening refresh: 14:50-15:50 ET = 13:50-14:50 Central, every 3 minutes.
$closingTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 13:50
$closingRepetition = New-CimInstance -CimClass $repetitionClass -ClientOnly
$closingRepetition.Interval = 'PT3M'
$closingRepetition.Duration = 'PT1H'
$closingRepetition.StopAtDurationEnd = $false
$closingTrigger.Repetition = $closingRepetition

$trigger = @($openingTrigger, $weekdayTrigger, $closingTrigger)

# 2026-08-13: RunLevel Highest (elevated) with no documented reason -- this
# task only browses a public website and writes to files under the user's
# own OneDrive folder, nothing here needs admin rights. Confirmed live and
# repeatedly (Start-ScheduledTask triggered twice on-demand, clean
# environment both times) that running elevated is actually what breaks it:
# every elevated run failed "session not created: Chrome instance exited"
# immediately after the browser window opened -- crashes before ever
# reaching trade-ideas.com -- while the identical script run from a normal,
# non-elevated shell succeeded every single time. Dropped to Limited
# (standard, non-elevated) to match what's actually been working.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:UserName" -LogonType Interactive -RunLevel Limited

# IgnoreNew: if a run is still going (or wedged) when the next 20-min slot fires,
# skip that slot rather than piling up overlapping Edge sessions.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $taskDescription

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    throw "Not running as Administrator. Right-click PowerShell and 'Run as administrator', then re-run this script."
}

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force -ErrorAction Stop
Write-Host "Scheduled task '$taskName' installed successfully. Use 'schtasks /query /tn $taskName' to inspect."
