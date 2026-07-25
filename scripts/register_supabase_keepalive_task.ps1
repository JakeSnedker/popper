# Registers the PopperSupabaseKeepalive Windows Scheduled Task.
# Runs scripts/supabase_keepalive.py once DAILY via the repo venv, in Jake's
# user context. Keeps the shared Supabase project from auto-pausing.
#
# Run once (from the repo root, any shell):
#     powershell -ExecutionPolicy Bypass -File scripts\register_supabase_keepalive_task.ps1
#
# Idempotent: re-running unregisters the old task first, then re-registers.

$ErrorActionPreference = "Stop"

$TaskName = "PopperSupabaseKeepalive"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Script   = Join-Path $RepoRoot "scripts\supabase_keepalive.py"

if (-not (Test-Path $Python)) { throw "venv python not found: $Python" }
if (-not (Test-Path $Script)) { throw "keepalive script not found: $Script" }

$action  = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $RepoRoot
# Daily at 08:00 local. Free-tier pause is 7 days of inactivity - daily has huge margin.
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Daily cheap Supabase read to prevent free-tier 7-day auto-pause (Popper shared alpha DB)." | Out-Null

Write-Host "Registered scheduled task '$TaskName' (daily 08:00)."
Write-Host "Test it now:  Start-ScheduledTask -TaskName $TaskName ; Get-ScheduledTaskInfo -TaskName $TaskName"
