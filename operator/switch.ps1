param([ValidateSet("on", "off", "toggle")][string]$State = "toggle")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Popper is not installed yet. Run operator\Popper-Setup.cmd first."
}
if ($State -eq "toggle") {
    $Current = & $Python (Join-Path $Root "scripts\popper_node.py") status --json | ConvertFrom-Json
    $State = if ($Current.switch -eq "off") { "on" } else { "off" }
}
& $Python (Join-Path $Root "scripts\popper_node.py") switch $State
exit $LASTEXITCODE