$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Popper is not installed yet. Run operator\Popper-Setup.cmd first."
}
& $Python (Join-Path $Root "scripts\popper_node.py") status
exit $LASTEXITCODE
