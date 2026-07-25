# Popper launcher — boots the API + dashboard and opens the browser.
# First run auto-installs deps and seeds example data. Close the two spawned
# windows (or this one) to stop. Double-click the desktop icon to run.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "== Popper ==" -ForegroundColor Cyan

# Free our ports so repeated launches don't collide with a stale instance.
function Stop-Port($port) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}
Stop-Port 8000
Stop-Port 5173

# 1. Python venv + deps
if (-not (Test-Path $py)) {
    Write-Host "Creating Python venv..." -ForegroundColor Yellow
    python -m venv .venv
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -e ".[dev]"
}

# 2. Seed alpha memory on first run
if (-not (Test-Path (Join-Path $root "popper.db"))) {
    Write-Host "Seeding alpha memory..." -ForegroundColor Yellow
    & $py -m popper.cli seed
}

# 3. Frontend deps
if (-not (Test-Path (Join-Path $root "gui\node_modules"))) {
    Write-Host "Installing dashboard deps..." -ForegroundColor Yellow
    Push-Location (Join-Path $root "gui")
    npm install --no-fund --no-audit
    Pop-Location
}

# 4. Launch API (background window)
Write-Host "Starting API on http://127.0.0.1:8000 ..." -ForegroundColor Green
Start-Process -FilePath $py `
    -ArgumentList "-m", "uvicorn", "popper.api.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload" `
    -WorkingDirectory $root -WindowStyle Minimized

# 5. Launch dashboard (background window)
Write-Host "Starting dashboard on http://localhost:5173 ..." -ForegroundColor Green
Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c", "npm", "run", "dev" `
    -WorkingDirectory (Join-Path $root "gui") -WindowStyle Minimized

# 6. Open browser
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173"

Write-Host "`nPopper is up. Dashboard: http://localhost:5173  |  API: http://127.0.0.1:8000/docs" -ForegroundColor Cyan
Write-Host "Close the two minimized windows to stop." -ForegroundColor DarkGray
