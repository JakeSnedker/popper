@echo off
REM ── Popper one-click launcher ──────────────────────────────────────────────
REM Double-click this file (or run `Popper.cmd` from any shell) to boot the
REM full stack: FastAPI backend (:8000) + React dashboard (:5173) + browser.
REM Wraps start.ps1 so Windows Explorer can launch it without a PowerShell prompt.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
