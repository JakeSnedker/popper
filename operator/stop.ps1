param([switch]$Force)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Popper is not installed yet."
}
$Arguments = @((Join-Path $Root "scripts\popper_node.py"), "stop")
if ($Force) { $Arguments += "--force" }
& $Python @Arguments
exit $LASTEXITCODE
