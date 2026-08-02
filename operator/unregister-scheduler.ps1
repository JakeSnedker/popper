$ErrorActionPreference = "Stop"
# schtasks, for the same non-elevated reason as register-scheduler.ps1.
schtasks.exe /delete /tn "PopperNode" /f 2>$null | Out-Null
Write-Host "PopperNode scheduler removed. The running node, if any, was not stopped."
