# ApexTrader Task Scheduler launcher
# Called by Windows Task Scheduler - avoids quoting issues with inline -Command
$ErrorActionPreference = 'Continue'

$BaseDir = Split-Path -Parent $PSScriptRoot
$Script  = "$BaseDir\autobot.py"
$Log     = "$BaseDir\autobot_scheduler.log"

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

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [TASK] ApexTraderAutoRun triggered by Task Scheduler" | Tee-Object -FilePath $Log -Append

& $Python $Script 2>&1 | Tee-Object -FilePath $Log -Append
# Without this, the script's own exit code always defaults to 0 regardless of
# what Python actually did - confirmed 2026-08-05: Task Scheduler kept
# reporting "return code 0" for a watchdog that died within 1s of starting,
# making every restart look clean when it wasn't. Propagate the real code.
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [TASK] python exited with code $LASTEXITCODE" | Tee-Object -FilePath $Log -Append
exit $LASTEXITCODE
