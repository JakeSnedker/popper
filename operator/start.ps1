param(
    [double]$Minutes = 25,
    [int]$Iterations = 20,
    [ValidateSet("", "explore", "balanced", "exploit", "auto")]
    [string]$Mode = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Popper is not installed yet. Run operator\Popper-Setup.cmd first."
}
$Arguments = @("start", "--minutes", $Minutes, "--iterations", $Iterations)
if ($Mode) {
    $Arguments += @("--mode", $Mode)
}
& $Python (Join-Path $Root "scripts\popper_node.py") @Arguments
exit $LASTEXITCODE
