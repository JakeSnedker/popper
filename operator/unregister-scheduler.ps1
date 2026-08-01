$ErrorActionPreference = "Stop"
Unregister-ScheduledTask -TaskName "PopperNode" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "PopperNode scheduler removed. The running node, if any, was not stopped."
