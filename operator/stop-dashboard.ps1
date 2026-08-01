$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$StateFile = Join-Path $Root ".popper\dashboard.json"
if (-not (Test-Path -LiteralPath $StateFile)) {
    Write-Host "Popper dashboard is already stopped."
    exit 0
}
try {
    $state = [System.IO.File]::ReadAllText($StateFile) | ConvertFrom-Json
    if ($state.pid) {
        & taskkill.exe /PID $state.pid /T /F 2>$null | Out-Null
    }
} finally {
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
}
Write-Host "Popper dashboard stopped."
