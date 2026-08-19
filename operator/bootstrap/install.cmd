@echo off
REM Popper node bootstrap - double-click this.
REM
REM Canonical copy: Projects\popper\operator\bootstrap\install.cmd
REM Published copy: https://www.hirejakesnedker.com/popper/install.cmd
REM
REM Fetches install.ps1 from the same host and runs it. Kept tiny on purpose:
REM everything that can go wrong should go wrong in one place, and that place
REM is the PowerShell script next door.

setlocal
set "PS1_URL=https://www.hirejakesnedker.com/popper/install.ps1"

echo.
echo   Popper node setup
echo   -----------------
echo   Fetching the installer...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $s = (Invoke-WebRequest -UseBasicParsing -Uri '%PS1_URL%').Content } catch { Write-Host ''; Write-Host 'Could not download the installer.' -ForegroundColor Red; Write-Host 'Check your internet connection and try again.' -ForegroundColor Yellow; Read-Host 'Press Enter to close'; exit 1 }; $f = Join-Path $env:TEMP 'popper-install.ps1'; Set-Content -LiteralPath $f -Value $s -Encoding UTF8; & $f"

endlocal
