param([Parameter(Mandatory = $true)][string]$Tag)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Setup = Join-Path $PSScriptRoot "setup.ps1"
$StateFile = Join-Path $Root ".popper\state.json"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is required for updates." }
if (-not (Test-Path -LiteralPath $Python)) { throw "Run operator\Popper-Setup.cmd first." }
if ((& git -C $Root status --porcelain --untracked-files=no)) {
    throw "Update refused because tracked files have local changes. Nothing was changed."
}

$wasRunning = $false
if (Test-Path -LiteralPath $StateFile) {
    try {
        $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        if ($state.pid) { $wasRunning = $null -ne (Get-Process -Id $state.pid -ErrorAction SilentlyContinue) }
    } catch { $wasRunning = $false }
}

Write-Host "Fetching private release tags..." -ForegroundColor Cyan
& git -C $Root fetch origin --tags
if ($LASTEXITCODE -ne 0) { throw "Could not fetch private release tags." }
& git -C $Root show-ref --verify --quiet "refs/tags/$Tag"
if ($LASTEXITCODE -ne 0) { throw "Release tag '$Tag' does not exist." }
$target = (& git -C $Root rev-parse "refs/tags/$Tag^{commit}")
if ($LASTEXITCODE -ne 0 -or -not $target) { throw "Release tag '$Tag' cannot be resolved." }
$current = (& git -C $Root rev-parse HEAD).Trim()
if ($current -eq $target.Trim()) {
    Write-Host "Already on release $Tag."
    exit 0
}

& (Join-Path $PSScriptRoot "stop.ps1")
if ($LASTEXITCODE -ne 0) { throw "The current node did not stop safely." }
& (Join-Path $PSScriptRoot "stop-dashboard.ps1")

try {
    Write-Host "Switching to signed/tagged source $Tag..." -ForegroundColor Cyan
    & git -C $Root switch --detach $Tag
    if ($LASTEXITCODE -ne 0) { throw "Git could not switch to $Tag." }
    & $Setup -Headless -SkipFullDatasetVerification
    if ($LASTEXITCODE -ne 0) { throw "Release $Tag failed setup or preflight." }
} catch {
    Write-Warning "Update failed. Rolling back to the previous commit."
    & git -C $Root switch --detach $current
    & $Setup -Headless -SkipFullDatasetVerification
    throw
}

Write-Host "Updated to $Tag. .env and market data were preserved." -ForegroundColor Green
if ($wasRunning) {
    & (Join-Path $PSScriptRoot "start.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Update passed, but the node did not restart." }
}
