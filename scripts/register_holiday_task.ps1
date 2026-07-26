# Registers the Popper holiday runner as a Windows Scheduled Task.
# Subscription router: official Claude.ai + ChatGPT/Codex logins.
# Self-heals: starts at logon + every 5h, but won't double-start (IgnoreNew).
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\register_holiday_task.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$bash = "C:\Program Files\Git\bin\bash.exe"
$repoFwd = $repo -replace '\\','/'   # git bash accepts C:/Users/... directly
$python = Join-Path $repo ".venv\Scripts\python.exe"
$authCheck = Join-Path $repo "scripts\check_subscription_auth.py"

& $python $authCheck
if ($LASTEXITCODE -ne 0) {
    throw "Both subscription CLIs must be authenticated before registering the worker."
}

$action = New-ScheduledTaskAction -Execute $bash `
    -Argument "-lc `"cd '$repoFwd' && exec bash scripts/holiday_worker.sh`""

# Two triggers: at logon (so it comes back after a reboot) + repeat every 5h as a self-heal.
$t1 = New-ScheduledTaskTrigger -AtLogOn
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 5)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero)   # no time limit; the loop manages itself

Register-ScheduledTask -TaskName "PopperHolidayRunner" `
    -Action $action -Trigger $t1,$t2 -Settings $settings `
    -Description "Popper Claude/OpenAI subscription alpha-generation runner. Logs to holiday.log." `
    -Force | Out-Null

Write-Host "Registered PopperHolidayRunner. Starting it now..."
Start-ScheduledTask -TaskName "PopperHolidayRunner"
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName "PopperHolidayRunner" | Select-Object TaskName,State
Write-Host "Watch progress:  Get-Content '$repo\holiday.log' -Wait"
