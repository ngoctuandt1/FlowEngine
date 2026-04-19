# Selectively kill Chrome processes spawned by FlowEngine.
#
# FlowEngine launches Chrome with `--user-data-dir` pointing to either
# `chrome-profiles\<profile>\` (warm_profile.py, FlowClient base) or
# `%TEMP%\flow_<profile>_<timestamp>` (FlowClient clone). The user's
# personal Chrome at `AppData\Local\Google\Chrome\User Data` must never
# be touched — see memory `feedback_chrome_kill_selective.md`.
#
# Usage (PowerShell or bash with `powershell -File`):
#     powershell -NoProfile -File scripts/kill_engine_chrome.ps1
#
# Prints each PID it terminates. Safe to run even when no engine Chrome
# is alive — Where-Object matches nothing and the pipeline is a no-op.

$ownedPattern = '\\chrome-profiles\\|\\flow_[a-zA-Z0-9]'

$targets = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -match $ownedPattern }

if (-not $targets) {
    Write-Host "No FlowEngine Chrome processes found."
    exit 0
}

foreach ($p in $targets) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host ("Killed PID {0}" -f $p.ProcessId)
    } catch {
        Write-Host ("Failed to kill PID {0}: {1}" -f $p.ProcessId, $_.Exception.Message)
    }
}
