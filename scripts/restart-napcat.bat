@echo off
REM Restart the QQ side (NapCat) when the bot goes silent.
REM The PowerShell script self-elevates, so a UAC prompt will appear.
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-napcat.ps1"
echo.
pause
