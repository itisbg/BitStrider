# Run this in an elevated (Administrator) PowerShell - right-click PowerShell,
# "Run as administrator", then paste/run this file.
#
# Moves ApexTraderTICapture's daily window from 06:00-20:00 to 03:00-20:00
# (3 AM CDT = 4 AM ET, real pre-market open) instead of 6 AM - closes the gap
# where the bot would otherwise wait until 6 AM before its first scrape of
# the day, well after pre-market activity has already started. End time
# (8 PM CDT) is unchanged. Everything else on the task (logon trigger,
# 20-min repeat cadence, interactive logon, 10-min per-run timeout) stays
# exactly as-is - only the CalendarTrigger's start time and duration change.

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
    <Description>Refresh data/ti_primary.json every 20 min, Mon-Fri 03:00-20:00 - single-shot runs owned by Task Scheduler</Description>
    <URI>\ApexTraderTICapture</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$sid</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
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
    <LogonTrigger />
    <CalendarTrigger>
      <StartBoundary>2026-08-06T03:00:00-05:00</StartBoundary>
      <Repetition>
        <Interval>PT20M</Interval>
        <Duration>PT17H</Duration>
      </Repetition>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
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
Write-Host "Task updated - now runs 03:00-20:00 CDT (was 06:00-20:00)." -ForegroundColor Green
(Get-ScheduledTask -TaskName 'ApexTraderTICapture').Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskCalendarTrigger' } | Select-Object StartBoundary
(Get-ScheduledTask -TaskName 'ApexTraderTICapture').Triggers[1].Repetition
Get-ScheduledTaskInfo -TaskName 'ApexTraderTICapture' | Select-Object NextRunTime
