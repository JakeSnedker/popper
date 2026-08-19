@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0providers.ps1" %*
pause
