# Run this in an elevated (Administrator) PowerShell - right-click PowerShell,
# "Run as administrator", then paste/run this file.
#
# Blunt kill of every msedge.exe / msedgedriver.exe currently running.
# Safe here: these were already traced back to the bot's own automation
# process tree, not a personal browsing window. If you're using Edge for
# something else right now, close/save that first.

$ErrorActionPreference = 'Continue'

$procs = Get-Process msedge, msedgedriver -ErrorAction SilentlyContinue
if (-not $procs) {
    Write-Host "Nothing to kill - already clean."
} else {
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "Killed $($procs.Count) process(es)." -ForegroundColor Green
}

Start-Sleep -Seconds 2
Write-Host "`nRemaining msedge/msedgedriver processes:" -ForegroundColor Yellow
$left = Get-Process msedge, msedgedriver -ErrorAction SilentlyContinue
if ($left) { $left | Select-Object Id, ProcessName, SessionId } else { Write-Host "None. Clean." -ForegroundColor Cyan }
