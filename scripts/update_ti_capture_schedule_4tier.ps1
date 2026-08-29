# Run this in an elevated (Administrator) PowerShell - right-click PowerShell,
# "Run as administrator", then paste/run this file.
#
# 2026-08-27, user request: restructure ApexTraderTICapture from 2 triggers
# (3 min 08:25-09:30, 10 min 09:30-14:50 local -- i.e. 9:25-10:30am then
# 10:30am-3:50pm ET) into 4:
#   09:25-10:30 ET  3 min  (unchanged -- the fast open-ramp window)
#   10:30-12:30 ET  5 min  (new)
#   12:30-14:50 ET  10 min (unchanged cadence, narrower window)
#   14:50-15:50 ET  3 min  (new -- last hour before ENTRY_WINDOW_END_ET,
#                           mimics the first hour's cadence)
# Total span (09:25-15:50 ET) and everything else about the task (logon
# trigger... note: logon trigger was actually removed from the live task at
# some point before this script was written -- only the two CalendarTriggers
# were live -- so this script also does NOT add one back, matching what's
# actually running) stays the same; only the Triggers block changes.

$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    throw "Not elevated. Right-click PowerShell -> Run as administrator, then re-run this script."
}

$sid = (Get-ScheduledTask -TaskName 'ApexTraderTICapture').Principal.UserId
if ($sid -notmatch '^S-1-5-21-') {
    $sid = (New-Object System.Security.Principal.NTAccount($sid)).Translate([System.Security.Principal.SecurityIdentifier]).Value
}

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Refresh data/ti_primary.json: every 3 min 08:25-09:30 (9:25-10:30am ET), every 5 min 09:30-11:30 (10:30am-12:30pm ET), every 10 min 11:30-13:50 (12:30-2:50pm ET), every 3 min 13:50-14:50 (2:50-3:50pm ET, last hour before ENTRY_WINDOW_END_ET), Mon-Fri -- single-shot runs owned by Task Scheduler</Description>
    <URI>\ApexTraderTICapture</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$sid</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <Duration>PT10M</Duration>
      <WaitTimeout>PT1H</WaitTimeout>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-08-24T08:25:00-05:00</StartBoundary>
      <Repetition>
        <Interval>PT3M</Interval>
        <Duration>PT1H5M</Duration>
      </Repetition>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek><Monday /><Tuesday /><Wednesday /><Thursday /><Friday /></DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-08-24T09:30:00-05:00</StartBoundary>
      <Repetition>
        <Interval>PT5M</Interval>
        <Duration>PT2H</Duration>
      </Repetition>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek><Monday /><Tuesday /><Wednesday /><Thursday /><Friday /></DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-08-24T11:30:00-05:00</StartBoundary>
      <Repetition>
        <Interval>PT10M</Interval>
        <Duration>PT2H20M</Duration>
      </Repetition>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek><Monday /><Tuesday /><Wednesday /><Thursday /><Friday /></DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-08-24T13:50:00-05:00</StartBoundary>
      <Repetition>
        <Interval>PT3M</Interval>
        <Duration>PT1H</Duration>
      </Repetition>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek><Monday /><Tuesday /><Wednesday /><Thursday /><Friday /></DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\BG\OneDrive\Returns-LSTM\StockPricePrediction\BitStrider-main\scripts\run_ti_capture_task.ps1"</Arguments>
    </Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName 'ApexTraderTICapture' -Xml $xml -Force | Out-Null
Write-Host "Task updated -- now 4 tiers: 3min/09:25-10:30 ET, 5min/10:30-12:30 ET, 10min/12:30-14:50 ET, 3min/14:50-15:50 ET." -ForegroundColor Green
(Get-ScheduledTask -TaskName 'ApexTraderTICapture').Triggers | Select-Object StartBoundary, @{N='Interval';E={$_.Repetition.Interval}}, @{N='Duration';E={$_.Repetition.Duration}}
Get-ScheduledTaskInfo -TaskName 'ApexTraderTICapture' | Select-Object NextRunTime
