param(
    [switch]$Install
)

# Reports every tool a Popper node needs, and exactly how to get the missing ones.
# Runs BEFORE setup.ps1 asks for a password: an operator should learn that Node.js
# is missing in five seconds, not after typing their Supabase login and waiting for
# a dependency install to fail. Safe to re-run at any time; without -Install it only
# reads. With -Install it asks per tool before running one npm command.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

$Base = @(
    @{ Name = "python"; Label = "Python 3.11+";
       Fix = "Install from https://www.python.org/downloads/ and TICK 'Add python.exe to PATH'." }
    @{ Name = "git"; Label = "Git";
       Fix = "Install from https://git-scm.com/download/win (accept the defaults)." }
    @{ Name = "node"; Label = "Node.js LTS";
       Fix = "Install the LTS build from https://nodejs.org (this also installs npm)." }
    @{ Name = "npm"; Label = "npm";
       Fix = "Comes with Node.js LTS from https://nodejs.org." }
)

# One of these three is enough to research; all three is roughly triple throughput.
# Package names are the ones actually published, verified against a working node.
$Llm = @(
    @{ Name = "claude"; Label = "Claude Code (Claude.ai subscription)"; Package = "@anthropic-ai/claude-code";
       Login = "Run 'claude', then type /login and sign in with your Claude.ai subscription (NOT an API key)." }
    @{ Name = "codex"; Label = "Codex (ChatGPT subscription)"; Package = "@openai/codex";
       Login = "Run 'codex login' and choose 'Sign in with ChatGPT'. An API-key login is metered and Popper rejects it." }
    @{ Name = "opencode"; Label = "OpenCode (free DeepSeek route)"; Package = "opencode-ai";
       Login = "No login needed. The free OpenCode Zen model is available once it is installed." }
)

function Find-Tool([string]$Name) {
    # Get-Command honours PATHEXT, so it finds npm's .cmd shims as well as .exe.
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { return $null }
    return $command.Source
}

function Get-PythonVersion {
    try {
        $text = (& python --version 2>&1) -join " "
    } catch { return $null }
    if ($text -match "(\d+)\.(\d+)\.(\d+)") {
        return [version]("{0}.{1}.{2}" -f $matches[1], $matches[2], $matches[3])
    }
    return $null
}

function Write-Row([string]$State, [string]$Label, [string]$Detail) {
    $colour = switch ($State) { "OK" { "Green" } "MISSING" { "Red" } default { "Yellow" } }
    Write-Host ("[{0,-7}] {1,-42} {2}" -f $State, $Label, $Detail) -ForegroundColor $colour
}

Write-Host "`nPopper node dependency check" -ForegroundColor Cyan
Write-Host ("=" * 78)

$missingBase = @()
foreach ($tool in $Base) {
    $path = Find-Tool $tool.Name
    if (-not $path) {
        Write-Row "MISSING" $tool.Label $tool.Fix
        $missingBase += $tool
        continue
    }
    if ($tool.Name -eq "python") {
        $version = Get-PythonVersion
        if (-not $version) {
            # Windows ships a python.exe alias that opens the Microsoft Store and
            # prints nothing. It looks installed and is not.
            Write-Row "MISSING" $tool.Label ("Found $path but it reported no version. " +
                "Settings > Apps > Advanced app settings > App execution aliases: turn OFF both python.exe entries, then reinstall Python.")
            $missingBase += $tool
            continue
        }
        if ($version -lt [version]"3.11") {
            Write-Row "MISSING" $tool.Label "Found $version; Popper needs 3.11 or newer. $($tool.Fix)"
            $missingBase += $tool
            continue
        }
        Write-Row "OK" $tool.Label "$version at $path"
        continue
    }
    Write-Row "OK" $tool.Label $path
}

$presentLlm = @()
$missingLlm = @()
foreach ($tool in $Llm) {
    $path = Find-Tool $tool.Name
    if ($path) {
        Write-Row "OK" $tool.Label $path
        $presentLlm += $tool
    } else {
        Write-Row "MISSING" $tool.Label "npm install -g $($tool.Package)"
        $missingLlm += $tool
    }
}

Write-Host ("=" * 78)

# Being installed is not being logged in. The venv owns the only honest answer to
# "is this subscription usable", so ask it rather than re-implementing the probe.
if ($presentLlm.Count -gt 0) {
    if (Test-Path -LiteralPath $Python) {
        Write-Host "`nSubscription logins:" -ForegroundColor Cyan
        & $Python (Join-Path $Root "scripts\check_subscription_auth.py")
        if ($LASTEXITCODE -ne 0) {
            Write-Host "`nNone of the installed CLIs is logged in yet. Log in, then re-run this check." -ForegroundColor Yellow
            foreach ($tool in $presentLlm) { Write-Host "  $($tool.Label): $($tool.Login)" }
        }
    } else {
        Write-Host "`nLogin state is checked after setup builds the Python environment." -ForegroundColor DarkGray
        Write-Host "Log in now anyway so setup passes first time:"
        foreach ($tool in $presentLlm) { Write-Host "  $($tool.Label): $($tool.Login)" }
    }
}

if ($Install -and $missingLlm.Count -gt 0) {
    if (-not (Find-Tool "npm")) {
        Write-Host "`nInstall Node.js LTS first; npm installs the AI CLIs." -ForegroundColor Yellow
    } else {
        # Never install without a yes. These are global installs on someone else's
        # machine and one of them may already exist under a different install method.
        foreach ($tool in $missingLlm) {
            $answer = Read-Host "`nInstall $($tool.Label)? (npm install -g $($tool.Package)) [y/N]"
            if ($answer -notmatch '^(y|yes)$') { Write-Host "Skipped."; continue }
            & npm install -g $tool.Package
            if ($LASTEXITCODE -ne 0) {
                Write-Host "npm could not install $($tool.Package). Nothing else was changed." -ForegroundColor Red
                continue
            }
            Write-Host "Installed. Next: $($tool.Login)" -ForegroundColor Green
        }
        Write-Host "`nOpen a NEW PowerShell window (so PATH refreshes) and run this check again." -ForegroundColor Yellow
    }
}

$failed = $false
if ($missingBase.Count -gt 0) {
    Write-Host "`nMissing required tools: $(($missingBase | ForEach-Object { $_.Name }) -join ', ')" -ForegroundColor Red
    $failed = $true
}
if ($presentLlm.Count -eq 0) {
    Write-Host "`nNo AI CLI found. Install at least one of: $(($Llm | ForEach-Object { $_.Package }) -join ', ')" -ForegroundColor Red
    Write-Host "Re-run with -Install to be walked through it: operator\Popper-Check.cmd -Install"
    $failed = $true
}
if ($failed) {
    Write-Host "`nNOT READY. Fix the red rows above, then run this again." -ForegroundColor Red
    exit 1
}

Write-Host "`nAll dependencies present. Next: operator\Popper-Setup.cmd" -ForegroundColor Green
exit 0
