# Run this in an elevated (Administrator) PowerShell - right-click PowerShell,
# "Run as administrator", then paste/run this file.
#
# Adds a second, slower overnight cadence to ApexTraderTICapture so it never
# fully stops: 20-min scrapes 03:00-20:00 (unchanged), every 2h the rest of
# the time so a genuine after-hours/overnight move still gets picked up.
#
# The overnight trigger's start time is computed as "right now" (whenever
# this script runs), not a fixed clock time - so it fires almost immediately
# tonight too, then repeats every 2h until it hits its 7h window, then
# recurs at that same time-of-day on subsequent weekdays going forward.

$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    throw "Not elevated. Right-click PowerShell -> Run as administrator, then re-run this script."
}

$sid = (Get-ScheduledTask -TaskName 'ApexTraderTICapture').Principal.UserId
if ($sid -notmatch '^S-1-5-21-') {
    $sid = (New-Object System.Security.Principal.NTAccount($sid)).Translate([System.Security.Principal.SecurityIdentifier]).Value
}

$overnightStart = (Get-Date).AddMinutes(2).ToString("yyyy-MM-ddTHH:mm:ss-05:00")
Write-Host "Overnight trigger will start firing at: $overnightStart" -ForegroundColor Cyan

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Refresh data/ti_primary.json: every 20 min 03:00-20:00, every 2h overnight - single-shot runs owned by Task Scheduler</Description>
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
    <CalendarTrigger>
      <StartBoundary>$overnightStart</StartBoundary>
      <Repetition>
        <Interval>PT2H</Interval>
        <Duration>PT7H</Duration>
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
Write-Host "Task updated." -ForegroundColor Green
(Get-ScheduledTask -TaskName 'ApexTraderTICapture').Triggers | ForEach-Object {
    [PSCustomObject]@{ Type = $_.CimClass.CimClassName; StartBoundary = $_.StartBoundary; Interval = $_.Repetition.Interval; Duration = $_.Repetition.Duration }
}
Write-Host "`nNext run:" -ForegroundColor Cyan
Get-ScheduledTaskInfo -TaskName 'ApexTraderTICapture' | Select-Object NextRunTime
