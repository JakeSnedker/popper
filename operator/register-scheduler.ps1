$ErrorActionPreference = "Stop"
$TaskName = "PopperNode"
$Root = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $PSScriptRoot "start.ps1"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) { throw "Run operator\Popper-Setup.cmd first." }
& $Python (Join-Path $Root "scripts\popper_node.py") preflight
if ($LASTEXITCODE -ne 0) { throw "Preflight must pass before scheduling the node." }

# Registered through schtasks.exe with a task XML rather than Register-ScheduledTask:
# the cmdlet writes via the TaskScheduler WMI namespace, which answers
# "Access is denied" on a standard (non-elevated) operator account. An operator
# must be able to schedule their own node without an admin prompt.
$PowerShell = (Get-Command powershell.exe).Source
$UserId = "$env:USERDOMAIN\$env:USERNAME"
$Start = (Get-Date).AddMinutes(1).ToString("yyyy-MM-ddTHH:mm:ss")
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""

$Xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts one Popper research node after login and self-heals every 15 minutes.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$UserId</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <Enabled>true</Enabled>
      <StartBoundary>$Start</StartBoundary>
      <Repetition>
        <Interval>PT15M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$UserId</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$PowerShell</Command>
      <Arguments>$Arguments</Arguments>
      <WorkingDirectory>$Root</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$XmlFile = Join-Path $env:TEMP "popper_node_task.xml"
[System.IO.File]::WriteAllText($XmlFile, $Xml, [System.Text.Encoding]::Unicode)
try {
    schtasks.exe /create /tn $TaskName /xml "$XmlFile" /f | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "schtasks refused to register $TaskName (exit $LASTEXITCODE)." }
} finally {
    Remove-Item -LiteralPath $XmlFile -ErrorAction SilentlyContinue
}
Write-Host "Registered $TaskName. Duplicate workers are blocked by the node lock." -ForegroundColor Green
