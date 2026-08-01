@echo off
REM Opens the local authenticated Popper dashboard.
REM Research workers are started separately through operator\Popper-Start.cmd.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
