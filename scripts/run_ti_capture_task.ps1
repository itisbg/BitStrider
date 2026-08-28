# Deprecated Trade Ideas task launcher.
# Yahoo Finance refresh now runs in-process from engine/orchestrator.py.
$ErrorActionPreference = 'Continue'

$BaseDir = Split-Path -Parent $PSScriptRoot
$Log     = "$BaseDir\ti_capture_scheduler.log"

# This folder syncs across multiple machines via OneDrive, so don't hardcode
# a machine/user-specific python.exe path here (it breaks on every other
# machine, or after a profile rename). Resolve via the launcher / PATH instead.
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [TASK] Deprecated ApexTraderTICapture invocation ignored; Yahoo refresh is owned by the running bot" | Tee-Object -FilePath $Log -Append
exit 0
