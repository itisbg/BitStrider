# ApexTrader TI Capture Task Scheduler launcher
# Called by Windows Task Scheduler - avoids quoting issues with inline -Command
$ErrorActionPreference = 'Continue'

$BaseDir = Split-Path -Parent $PSScriptRoot
$Script  = "$BaseDir\engine\ti\capture_tradeideas.py"
$Log     = "$BaseDir\ti_capture_scheduler.log"

# This folder syncs across multiple machines via OneDrive, so don't hardcode
# a machine/user-specific python.exe path here (it breaks on every other
# machine, or after a profile rename). Resolve via the launcher / PATH instead.
$Python = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $Python) { $Python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $Python) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [TASK] ERROR: no Python interpreter found on PATH (py/python)" | Tee-Object -FilePath $Log -Append
    exit 1
}

Set-Location $BaseDir

# Dedicated automation-only profile (2026-08-06) — was "Default", which is
# also whatever a normal, hand-opened Edge window uses. Sharing one
# exclusive-lock profile between the scraper and anything else (your own
# browsing, or the scraper's own half-crashed leftover from a prior cycle)
# blocked every scrape, all day. Nothing but this task should ever open it.
$TiProfile = "TIAutomation"

# Proactive cleanup, not just capture_tradeideas.py's own reactive one-shot
# retry: a failed session-creation attempt can leave the just-spawned Edge
# process running even though Selenium reports failure, so a bad cycle can
# exit having created *more* zombies than it cleaned up. Clear any leftover
# automation Edge before every launch so each cycle starts from a known-clean
# profile lock. Matched the same safe way as capture_tradeideas.py's own
# _kill_orphaned_automation_edge(): only processes carrying Selenium's
# "--test-type=webdriver" marker on this profile — this can never match a
# real Edge window opened by hand.
Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.CommandLine -match '--test-type=webdriver' -and $_.CommandLine -notmatch '--type=' -and $_.CommandLine -match "--profile-directory=$TiProfile") {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
    }
}

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [TASK] ApexTraderTICapture triggered by Task Scheduler" | Tee-Object -FilePath $Log -Append

# Single-shot (no --loop): Task Scheduler's own 15-min repetition trigger owns the
# cadence now, not an internal Python loop that can silently die and stay dead
# until the next log on (see 2026-08-03 Edge-profile-lock crash).
& $Python $Script --update-config --chrome-profile $TiProfile 2>&1 | Tee-Object -FilePath $Log -Append
