# Windows Scheduled Task Install for the TI Capture scraper
# Run in PowerShell as Administrator.

$taskName = 'ApexTraderTICapture'
$taskDescription = 'Refresh data/ti_primary.json: every 5 min 08:25-09:30 (9:25-10:30am ET), then every 10 min until 14:50 (3:50pm ET, matches ENTRY_WINDOW_END_ET), Mon-Fri — single-shot runs owned by Task Scheduler'
$BaseDir = $PSScriptRoot

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BaseDir\scripts\run_ti_capture_task.ps1`""

# 2026-08-22, user request: the FIRST trade-ideas.com call of the day must land
# at 08:30 (market open, this machine's Central clock) — not earlier. Dropped
# the old AtLogOn trigger: it fired an immediate scrape at whatever time the
# user happened to log into Windows, which could be well before 08:30 and made
# "first call" unpredictable. The weekly trigger below is Task-Scheduler-owned
# (not an internal Python loop), so it re-arms every cycle on its own even
# after a crashed run — no logon-trigger safety net needed for that anymore.
$repetitionClass = Get-CimClass -ClassName MSFT_TaskRepetitionPattern -Namespace Root/Microsoft/Windows/TaskScheduler

# 2026-08-22, user request: "trade ideas scrapping for the time 9.25am to
# 10.30 am will be every 5minutes" -- single trigger covering the whole
# 08:25-09:30 Central window (9:25-10:30am ET), every 5 min, first fire
# doubling as the old separate kickstart run (was a one-shot 08:25 trigger
# plus a separate 08:30-09:30 repeating one; merged since the kickstart
# time is now just this block's first tick).
$openingTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:25
# .Repetition isn't settable in place (returns a fresh, disconnected CIM instance
# each access) — build the repetition pattern separately and assign it whole.
$openingRepetition = New-CimInstance -CimClass $repetitionClass -ClientOnly
$openingRepetition.Interval = 'PT5M'
$openingRepetition.Duration = 'PT1H5M'   # 08:25 -> 09:30 (9:25-10:30am ET)
$openingRepetition.StopAtDurationEnd = $false
$openingTrigger.Repetition = $openingRepetition

# Rest of the session: back to every 10 min, 09:30 -> 14:50 Central (3:50pm
# ET, matches ENTRY_WINDOW_END_ET/EOD_CLOSE_TIME in config.py -- no new
# entries fire after that, so fresh universe data past that point is
# pointless). 2026-08-22, user request: "even the webscrapping for universe
# should stop at 3.50ET" -- was PT5H30M out to 15:00 Central (market close,
# 4:00pm ET), which ran 10 min past the point entries actually stop.
$weekdayTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:30
$repetition = New-CimInstance -CimClass $repetitionClass -ClientOnly
$repetition.Interval = 'PT10M'
$repetition.Duration = 'PT5H20M'   # 09:30 -> 14:50 (3:50pm ET)
$repetition.StopAtDurationEnd = $false
$weekdayTrigger.Repetition = $repetition

$trigger = @($openingTrigger, $weekdayTrigger)

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
