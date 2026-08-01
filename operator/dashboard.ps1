param(
    [switch]$NoBrowser,
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Dist = Join-Path $Root "gui\dist\index.html"
$StateDir = Join-Path $Root ".popper"
$LogsDir = Join-Path $StateDir "logs"
$StateFile = Join-Path $StateDir "dashboard.json"

if (-not (Test-Path -LiteralPath $Python)) { throw "Run operator\Popper-Setup.cmd first." }
if (-not (Test-Path -LiteralPath $Dist)) { throw "Dashboard build missing. Run operator\Popper-Setup.cmd." }
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

function Test-PopperHealth([int]$Candidate) {
    try {
        $result = Invoke-RestMethod -Uri "http://127.0.0.1:$Candidate/api/health" -TimeoutSec 2
        return $result.ok -eq $true
    } catch { return $false }
}

if (Test-Path -LiteralPath $StateFile) {
    try {
        $prior = [System.IO.File]::ReadAllText($StateFile) | ConvertFrom-Json
        $priorProcess = Get-Process -Id $prior.pid -ErrorAction SilentlyContinue
        if ($priorProcess -and (Test-PopperHealth ([int]$prior.port))) {
            $Port = [int]$prior.port
            if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
            Write-Host "Popper dashboard: http://127.0.0.1:$Port" -ForegroundColor Green
            exit 0
        }
    } catch { }
}

$selected = $null
foreach ($candidate in $Port..($Port + 10)) {
    $listener = Get-NetTCPConnection -LocalPort $candidate -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) { $selected = $candidate; break }
}
if ($null -eq $selected) { throw "No free dashboard port found between $Port and $($Port + 10)." }
$Port = $selected

$StdoutLog = Join-Path $LogsDir "dashboard-$Port.log"
$StderrLog = Join-Path $LogsDir "dashboard-$Port.err.log"
$process = Start-Process -FilePath $Python `
    -ArgumentList "-m", "uvicorn", "docker_serve:app", "--host", "127.0.0.1", "--port", "$Port" `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
@{ pid = $process.Id; port = $Port; started_at = [DateTime]::UtcNow.ToString("o") } |
    ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    if (Test-PopperHealth $Port) { $ready = $true; break }
    if ($process.HasExited) { break }
}
if (-not $ready) { throw "Dashboard failed to start. See $StderrLog" }

if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
Write-Host "Popper dashboard: http://127.0.0.1:$Port" -ForegroundColor Green
if ($Port -ne 8000) { Write-Host "Port 8000 was busy, so Popper safely used $Port instead." -ForegroundColor Yellow }
Write-Host "Stop it with operator\stop-dashboard.ps1" -ForegroundColor DarkGray
