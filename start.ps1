# Backward-compatible local dashboard launcher.
# Node research is controlled separately through operator\Popper-Start.cmd.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
& (Join-Path $Root "operator\dashboard.ps1")
exit $LASTEXITCODE
