<#
  Popper node bootstrap.

  Canonical copy: Projects\popper\operator\bootstrap\install.ps1
  Published copy: https://www.hirejakesnedker.com/popper/install.ps1

  Turns a bare Windows machine into a running Popper node. It installs the three
  base tools, clones the repo at a release tag, and hands over to the existing
  operator\Popper-Setup.cmd -- it does not reimplement setup.

  It deliberately does NOT gate access. The private repo is the gate: a clone by
  anyone who is not a collaborator fails at GitHub, and the dataset and Supabase
  account are separately controlled. A public URL here is not a public Popper.
#>
[CmdletBinding()]
param(
    [string]$Tag = "v0.2.5-alpha",
    [string]$Path = "C:\Popper",
    [string]$Repo = "https://github.com/JakeSnedker/popper.git"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Say([string]$text, [string]$colour = "Gray") { Write-Host $text -ForegroundColor $colour }
function Step([string]$text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok([string]$text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Warn([string]$text) { Write-Host "   !!  $text" -ForegroundColor Yellow }

function Stop-With([string]$problem, [string]$fix) {
    Write-Host ""
    Write-Host "STOPPED: $problem" -ForegroundColor Red
    Write-Host "What to do: $fix" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Nothing was left half-installed. Fix that one thing and run this again."
    Read-Host "Press Enter to close"
    exit 1
}

trap {
    # Without this an unhandled error closes the window instantly and the
    # operator sees a black flash and no folder. Show it and hold the window.
    Write-Host ""
    Write-Host "UNEXPECTED ERROR" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Nothing was left half-installed. Send this text to Jake."
    Read-Host "Press Enter to close"
    exit 1
}

function Update-PathFromRegistry {
    # winget puts new tools on PATH for NEW processes; this one already started.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Have([string]$exe) { return [bool](Get-Command $exe -ErrorAction SilentlyContinue) }

function Ensure-Tool {
    param([string]$Exe, [string]$WingetId, [string]$Label)
    if (Have $Exe) { Ok "$Label already installed"; return }
    Say "   installing $Label (this takes a few minutes)..."
    winget install --id $WingetId --silent --accept-source-agreements --accept-package-agreements --disable-interactivity | Out-Null
    Update-PathFromRegistry
    if (-not (Have $Exe)) {
        Stop-With "$Label did not install." "Install $Label by hand from its website, then run this again."
    }
    Ok "$Label installed"
}

Clear-Host
Say "==============================================" Cyan
Say "  Popper node setup" Cyan
Say "==============================================" Cyan
Say ""
Say "This turns this computer into a Popper research node."
Say ""

# A re-run after a failure skips everything already done. Saying "45 minutes"
# again makes operators think they are starting over, so measure and say so.
$alreadyHasTools = (Have "python") -and (Have "git") -and (Have "node")
$alreadyHasCode = Test-Path -LiteralPath (Join-Path $Path ".git")
if ($alreadyHasTools -or $alreadyHasCode) {
    Say "Picking up where you left off - the slow parts are already done." Green
    if ($alreadyHasTools) { Say "  Python, Git and Node.js: already installed" }
    if ($alreadyHasCode) { Say "  Popper code: already downloaded" }
    Say ""
    Say "This should only take a few minutes."
} else {
    Say "It takes about 45 minutes, and most of that is downloads."
}
Say ""
Say "You will be asked to sign in to GitHub and to your AI"
Say "subscription. Those sign-ins happen in your browser and"
Say "this script never sees your passwords."
Say ""
Read-Host "Press Enter to begin"

Step "Checking Windows"
if ([Environment]::OSVersion.Version.Major -lt 10) {
    Stop-With "This needs Windows 10 or 11." "Use a newer machine."
}
Ok "Windows version fine"

$qualifier = Split-Path -Qualifier $Path
$driveName = $qualifier.TrimEnd(":")
$freeGb = [math]::Round((Get-PSDrive -Name $driveName).Free / 1GB, 1)
if ($freeGb -lt 10) {
    Stop-With "Only $freeGb GB free on $qualifier" "Free up space so there is at least 10 GB, then run this again."
}
Ok "$freeGb GB free on $qualifier"

if (-not (Have "winget")) {
    Stop-With "Windows Package Manager (winget) is missing." "Install 'App Installer' from the Microsoft Store, then run this again."
}
Ok "winget present"

Step "Installing the base tools"
Ensure-Tool -Exe "python" -WingetId "Python.Python.3.12" -Label "Python"
Ensure-Tool -Exe "git"    -WingetId "Git.Git"            -Label "Git"
Ensure-Tool -Exe "node"   -WingetId "OpenJS.NodeJS.LTS"  -Label "Node.js"

# The Microsoft Store python alias shadows a real install and breaks everything
# downstream. This is the single most common Popper setup failure, so catch it here.
$pyPath = (Get-Command python).Source
if ($pyPath -like "*WindowsApps*") {
    Stop-With "Windows is redirecting 'python' to the Microsoft Store." "Settings > Apps > Advanced app settings > App execution aliases: turn OFF both 'App Installer: python.exe' entries, then run this again."
}
Ok "python is a real install"

Step "Getting the code"
if (Test-Path -LiteralPath (Join-Path $Path ".git")) {
    Ok "Popper already present at $Path"
    Say "   updating to $Tag ..."
    git -C $Path fetch --tags --quiet
    git -C $Path checkout --quiet $Tag
} else {
    if (Test-Path -LiteralPath $Path) {
        $existing = @(Get-ChildItem -LiteralPath $Path -Force)
        if ($existing.Count -gt 0) {
            Stop-With "$Path already exists and is not empty." "Move or delete that folder, then run this again."
        }
    }
    Say ""
    Say "   A browser window may open asking you to sign in to GitHub." Yellow
    Say "   Sign in with the account Jake invited to the repository." Yellow
    Say ""
    git clone --branch $Tag --depth 1 $Repo $Path
    if ($LASTEXITCODE -ne 0) {
        Stop-With "Could not download the code from GitHub." "This almost always means the repository invite has not been accepted. Check your GitHub notifications, accept the invite, then run this again."
    }
}
Ok "Code is at $Path ($Tag)"

Step "Signing in to your AI subscription"
Say "   Popper runs on a ChatGPT or Claude.ai subscription."
Say "   One is enough. Do not buy the other for this."
Say ""
Say "   1) I have ChatGPT"
Say "   2) I have Claude.ai"
Say "   3) I have both"
Say "   4) Skip - I will do this myself later"
$choice = (Read-Host "   Which do you have? (1-4)").Trim()

function Install-Codex {
    Say "   installing the ChatGPT CLI ..."
    & npm install -g "@openai/codex" --loglevel=error | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Stop-With "The ChatGPT CLI did not install." "Check your internet connection and run this again."
    }
    Update-PathFromRegistry
    Say ""
    Say "   A browser will open. Choose 'Sign in with ChatGPT'." Yellow
    Say "   Do NOT paste an API key - Popper rejects those, they bill per use." Yellow
    Say ""
    codex login
}

function Install-Claude {
    Say "   installing the Claude CLI ..."
    & npm install -g "@anthropic-ai/claude-code" --loglevel=error | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Stop-With "The Claude CLI did not install." "Check your internet connection and run this again."
    }
    Update-PathFromRegistry
    Say ""
    Say "   Sign in with your Claude.ai subscription, not an API key." Yellow
    Say ""
    claude
}

switch ($choice) {
    "1" { Install-Codex }
    "2" { Install-Claude }
    "3" { Install-Codex; Install-Claude }
    default { Warn "Skipped. Popper cannot research until you sign in to one of them." }
}

Step "Checking for the market data"
$dataDir = Join-Path $Path "data\ftmo-us30-v1"
if (Test-Path -LiteralPath (Join-Path $dataDir "dataset-manifest.json")) {
    Ok "Dataset found"
} else {
    Warn "Dataset not here yet. That is expected - it is a separate 1.4 GB download."
    Say ""
    Say "   Download the 'ftmo-us30-v1' folder from the Google Drive link Jake sent,"
    Say "   then extract it so this exact file exists:"
    Say ""
    Say "     $dataDir\dataset-manifest.json" Yellow
    Say ""
    Say "   If you end up with ftmo-us30-v1\ftmo-us30-v1\... move it up one level."
    Say ""
    Say "   Popper only ever tests on data it can verify on this machine, so it"
    Say "   will simply wait rather than run on anything it cannot check."
    Say ""
}

Step "Handing over to Popper's own setup"
Say "   The next window asks for your Supabase login, the project key,"
Say "   your operator name and a Machine ID. Jake sent you those."
Say ""
Read-Host "   Press Enter to open it"
& (Join-Path $Path "operator\Popper-Setup.cmd")

Write-Host ""
Say "==============================================" Cyan
Say "  Bootstrap finished." Cyan
Say "==============================================" Cyan
Say ""
Say "If every row in that window said PASS and the last line said READY,"
Say "you are done - double-click Popper-Start.cmd to begin researching."
Say ""
Say "If a row said FAIL, fix only that row and double-click Popper-Preflight.cmd."
Say "If the same row fails twice, stop and message Jake."
Say ""
Read-Host "Press Enter to close"
