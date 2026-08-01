@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 echo.
if errorlevel 1 echo Setup did not complete. Read the error above.
pause
