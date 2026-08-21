param(
    [switch]$Headless,
    [switch]$SkipInstall,
    [switch]$SkipFullDatasetVerification
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"
$ExampleEnv = Join-Path $Root ".env.example"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$DatasetId = "ftmo-us30-v1@7dce1c0c4e6ff630b8a0835bee05183f7902b6bb5a1753a93a600e62f0278c13"
$ProjectUrl = "https://YOUR_PROJECT_REF.supabase.co"

function Get-EnvValues([string]$Path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
            $value = $matches[2].Trim()
            if (($value.StartsWith("'") -and $value.EndsWith("'")) -or
                ($value.StartsWith('"') -and $value.EndsWith('"'))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $values[$matches[1]] = $value
        }
    }
    return $values
}

function Quote-EnvValue([string]$Value) {
    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "Configuration values cannot contain line breaks."
    }
    $escaped = $Value.Replace("'", "\'")
    return "'$escaped'"
}

function Set-EnvValues([hashtable]$Values) {
    $source = if (Test-Path -LiteralPath $EnvFile) { $EnvFile } else { $ExampleEnv }
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.AddRange([System.IO.File]::ReadAllLines($source))
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match '^([A-Z][A-Z0-9_]*)=') {
            $key = $matches[1]
            if ($Values.ContainsKey($key)) {
                $lines[$index] = "$key=$($Values[$key])"
                [void]$seen.Add($key)
            }
        }
    }
    foreach ($key in $Values.Keys) {
        if (-not $seen.Contains($key)) { $lines.Add("$key=$($Values[$key])") }
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($EnvFile, $lines, $utf8)
}

function Get-ReleaseId {
    $tags = @(& git -C $Root tag --points-at HEAD --list "v*")
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect Popper release tags." }
    if ($tags.Count -gt 0 -and $tags[0]) { return $tags[0].Trim() }
    $commit = (& git -C $Root rev-parse --short HEAD)
    if ($LASTEXITCODE -ne 0 -or -not $commit) { throw "Cannot determine Popper release identity." }
    return $commit.Trim()
}

function Protect-EnvFile {
    & icacls $EnvFile /inheritance:r /grant:r `
        "$($env:USERDOMAIN)\$($env:USERNAME):(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict .env permissions." }
}

function Require-Commands {
    # One dependency check, in one place: check-deps.ps1 also runs standalone as
    # operator\Popper-Check.cmd, so the operator can fix a missing tool before
    # setup ever asks for a password. It never installs anything without -Install.
    & (Join-Path $PSScriptRoot "check-deps.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw ("Prerequisites are missing. Run operator\Popper-Check.cmd -Install, " +
               "then run setup again. See docs\OPERATOR_ONBOARDING.md.")
    }
}

function New-DesktopShortcuts {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $items = @{
        "Popper Dashboard" = "Popper-Dashboard.cmd"
        "Popper Node Start" = "Popper-Start.cmd"
        "Popper Node Status" = "Popper-Status.cmd"
        "Popper Node Stop" = "Popper-Stop.cmd"
    }
    foreach ($name in $items.Keys) {
        $shortcut = $shell.CreateShortcut((Join-Path $desktop "$name.lnk"))
        $shortcut.TargetPath = Join-Path $PSScriptRoot $items[$name]
        $shortcut.WorkingDirectory = $Root
        $icon = Join-Path $Root "assets\popper.ico"
        if (Test-Path -LiteralPath $icon) { $shortcut.IconLocation = $icon }
        $shortcut.Save()
    }
}

# Checked first, before the form: nobody should type a Supabase password and wait
# through a dependency install only to be told Node.js was never installed.
Require-Commands

$existing = Get-EnvValues $EnvFile
$createShortcuts = $false
if (-not $Headless) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Popper Node Setup"
    $form.Size = New-Object System.Drawing.Size(700, 570)
    $form.StartPosition = "CenterScreen"
    $form.MaximizeBox = $false
    $form.FormBorderStyle = "FixedDialog"

    $title = New-Object System.Windows.Forms.Label
    $title.Text = "Connect this Windows machine to the private Popper team"
    $title.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(24, 20)
    $form.Controls.Add($title)

    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Text = "Nothing is uploaded by this wizard. Credentials are written only to the local ignored .env file."
    $subtitle.AutoSize = $true
    $subtitle.ForeColor = [System.Drawing.Color]::DimGray
    $subtitle.Location = New-Object System.Drawing.Point(27, 55)
    $form.Controls.Add($subtitle)

    function Add-Field([string]$Label, [int]$Top, [string]$Value, [bool]$Secret = $false) {
        $caption = New-Object System.Windows.Forms.Label
        $caption.Text = $Label
        $caption.AutoSize = $true
        $caption.Location = New-Object System.Drawing.Point(28, $Top)
        $form.Controls.Add($caption)
        $box = New-Object System.Windows.Forms.TextBox
        $box.Size = New-Object System.Drawing.Size(440, 24)
        $box.Location = New-Object System.Drawing.Point(205, ($Top - 4))
        $box.Text = $Value
        $box.UseSystemPasswordChar = $Secret
        $form.Controls.Add($box)
        return $box
    }

    $url = Add-Field "Supabase project URL" 100 $ProjectUrl
    $url.ReadOnly = $true
    $key = Add-Field "Publishable / anon key" 142 ($existing["POPPER_SUPABASE_KEY"])
    $email = Add-Field "Your Supabase email" 184 ($existing["POPPER_SUPABASE_EMAIL"])
    $password = Add-Field "Your Supabase password" 226 "" $true
    $operator = Add-Field "Operator label" 268 ($existing["POPPER_OPERATOR_ID"])
    if (-not $operator.Text) { $operator.Text = $env:USERNAME.ToLowerInvariant() }
    $machine = Add-Field "Machine ID" 310 ($existing["POPPER_MACHINE_ID"])
    if (-not $machine.Text) {
        $machine.Text = "$($env:USERNAME)-$($env:COMPUTERNAME)".ToLowerInvariant()
    }
    $datasetDefault = if ($existing["POPPER_DATASET_DIR"]) {
        $existing["POPPER_DATASET_DIR"]
    } else {
        Join-Path $Root "data\ftmo-us30-v1"
    }
    $dataset = Add-Field "Dataset folder" 352 $datasetDefault
    $browse = New-Object System.Windows.Forms.Button
    $browse.Text = "Browse"
    $browse.Size = New-Object System.Drawing.Size(80, 25)
    $browse.Location = New-Object System.Drawing.Point(565, 379)
    $browse.Add_Click({
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.SelectedPath = $dataset.Text
        if ($dialog.ShowDialog() -eq "OK") { $dataset.Text = $dialog.SelectedPath }
    })
    $form.Controls.Add($browse)

    $shortcuts = New-Object System.Windows.Forms.CheckBox
    $shortcuts.Text = "Create desktop shortcuts"
    $shortcuts.Checked = $true
    $shortcuts.AutoSize = $true
    $shortcuts.Location = New-Object System.Drawing.Point(205, 410)
    $form.Controls.Add($shortcuts)

    $notice = New-Object System.Windows.Forms.Label
    $notice.Text = "The setup will install project dependencies, build the dashboard, verify all 1,936 dataset files,`nand test Supabase plus Claude/Codex subscription access. It does not start research."
    $notice.AutoSize = $true
    $notice.ForeColor = [System.Drawing.Color]::DimGray
    $notice.Location = New-Object System.Drawing.Point(28, 450)
    $form.Controls.Add($notice)

    $install = New-Object System.Windows.Forms.Button
    $install.Text = "Save and verify this node"
    $install.Size = New-Object System.Drawing.Size(210, 38)
    $install.Location = New-Object System.Drawing.Point(435, 490)
    $install.Add_Click({
        if (-not $key.Text.Trim() -or -not $email.Text.Trim() -or -not $password.Text -or
            -not $operator.Text.Trim() -or -not $machine.Text.Trim() -or -not $dataset.Text.Trim()) {
            [System.Windows.Forms.MessageBox]::Show("Complete every field before continuing.", "Popper setup") | Out-Null
            return
        }
        if (-not (Test-Path -LiteralPath (Join-Path $dataset.Text "dataset-manifest.json"))) {
            [System.Windows.Forms.MessageBox]::Show("The selected folder has no dataset-manifest.json.", "Popper setup") | Out-Null
            return
        }
        $script:Collected = @{
            Url = $url.Text.Trim(); Key = $key.Text.Trim(); Email = $email.Text.Trim()
            Password = $password.Text; Operator = $operator.Text.Trim()
            Machine = $machine.Text.Trim(); Dataset = $dataset.Text.Trim()
        }
        $script:createShortcuts = $shortcuts.Checked
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    })
    $form.Controls.Add($install)

    if ($form.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        Write-Host "Setup cancelled."
        exit 1
    }

    $release = Get-ReleaseId
    Set-EnvValues @{
        "POPPER_STORE" = "supabase"
        "POPPER_SUPABASE_URL" = (Quote-EnvValue $Collected.Url)
        "POPPER_SUPABASE_KEY" = (Quote-EnvValue $Collected.Key)
        "POPPER_SUPABASE_EMAIL" = (Quote-EnvValue $Collected.Email)
        "POPPER_SUPABASE_PASSWORD" = (Quote-EnvValue $Collected.Password)
        "POPPER_AUTH_REQUIRED" = "true"
        "POPPER_OPERATOR_ID" = (Quote-EnvValue $Collected.Operator)
        "POPPER_MACHINE_ID" = (Quote-EnvValue $Collected.Machine)
        "POPPER_RELEASE_ID" = (Quote-EnvValue $release)
        "POPPER_DATASET_ID" = (Quote-EnvValue $DatasetId)
        "POPPER_DATASET_DIR" = (Quote-EnvValue $Collected.Dataset)
    }
    Protect-EnvFile
} elseif (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Headless setup requires an existing .env file."
}

if (-not $SkipInstall) {
    if (-not (Test-Path -LiteralPath $Python)) {
        Write-Host "Creating private Python environment..." -ForegroundColor Cyan
        & python -m venv (Join-Path $Root ".venv")
    }
    Write-Host "Installing Popper Python dependencies..." -ForegroundColor Cyan
    & $Python -m pip install --disable-pip-version-check --quiet --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    & $Python -m pip install --disable-pip-version-check --quiet -e "$Root[supabase]"
    if ($LASTEXITCODE -ne 0) { throw "Popper Python installation failed" }

    Write-Host "Installing and building the dashboard..." -ForegroundColor Cyan
    $Gui = Join-Path $Root "gui"
    & npm --prefix $Gui ci --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) {
        # npm ci refuses ANY lockfile drift, which strands an operator on a
        # problem only this repo can fix. Re-resolve and keep going instead.
        Write-Host "npm ci failed; re-resolving with npm install..." -ForegroundColor Yellow
        & npm --prefix $Gui install --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw "Dashboard dependency installation failed" }
    }
    & npm --prefix (Join-Path $Root "gui") run build
    if ($LASTEXITCODE -ne 0) { throw "Dashboard build failed" }
}

$release = Get-ReleaseId
Set-EnvValues @{ "POPPER_RELEASE_ID" = (Quote-EnvValue $release) }
Protect-EnvFile

Write-Host "Running Popper preflight..." -ForegroundColor Cyan
$preflightArgs = @((Join-Path $Root "scripts\popper_node.py"), "preflight")
if (-not $SkipFullDatasetVerification) { $preflightArgs += "--full-dataset" }
& $Python @preflightArgs
if ($LASTEXITCODE -ne 0) { throw "Preflight failed. Nothing was started." }

if ($createShortcuts) { New-DesktopShortcuts }
Write-Host "`nThis node is installed and ready. Research has not started." -ForegroundColor Green
Write-Host "Use operator\Popper-Start.cmd when you are ready, then operator\Popper-Status.cmd to see it."
