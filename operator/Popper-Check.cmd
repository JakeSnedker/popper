@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-deps.ps1" %*
pause
