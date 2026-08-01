$ErrorActionPreference = "Stop"
$TaskName = "PopperNode"
$Root = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $PSScriptRoot "start.ps1"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) { throw "Run operator\Popper-Setup.cmd first." }
& $Python (Join-Path $Root "scripts\popper_node.py") preflight
if ($LASTEXITCODE -ne 0) { throw "Preflight must pass before scheduling the node." }

$PowerShell = (Get-Command powershell.exe).Source
$Action = New-ScheduledTaskAction -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`"" `
    -WorkingDirectory $Root
$AtLogon = New-ScheduledTaskTrigger -AtLogOn
$SelfHeal = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $AtLogon,$SelfHeal `
    -Settings $Settings -Principal $Principal -Force `
    -Description "Starts one Popper research node after login and self-heals every 15 minutes." | Out-Null
Write-Host "Registered $TaskName. Duplicate workers are blocked by the node lock." -ForegroundColor Green
