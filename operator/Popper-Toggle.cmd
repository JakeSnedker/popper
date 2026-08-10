@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0switch.ps1" toggle
pause