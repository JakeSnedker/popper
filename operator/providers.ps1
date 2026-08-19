param(
    [ValidateSet("claude_cli", "codex_cli", "opencode_cli")][string]$Provider,
    [ValidateSet("on", "off")][string]$State
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Popper is not installed yet. Run operator\Popper-Setup.cmd first."
}
$Node = Join-Path $Root "scripts\popper_node.py"

if ($Provider -and $State) {
    & $Python $Node providers $Provider $State
    exit $LASTEXITCODE
}

# ponytail: a numbered prompt, not a menu framework. Three providers will not grow.
$names = @{ "1" = "claude_cli"; "2" = "codex_cli"; "3" = "opencode_cli" }
while ($true) {
    Write-Host ""
    & $Python $Node providers
    Write-Host ""
    Write-Host "  1) Claude            2) ChatGPT (Codex)     3) DeepSeek (OpenCode)"
    Write-Host "  Enter a number to flip it, or press Enter to finish."
    $choice = (Read-Host "Choice").Trim()
    if (-not $choice) { break }
    if (-not $names.ContainsKey($choice)) { Write-Host "Pick 1, 2, 3, or Enter."; continue }
    $target = $names[$choice]
    $current = & $Python $Node providers --json | ConvertFrom-Json
    $next = if ($current.parked -contains $target) { "on" } else { "off" }
    & $Python $Node providers $target $next
}
