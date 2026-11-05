# Install autostart via HKCU Run (no file is written to C:).
# The value points at scripts\supervise.ps1 so the bot comes back after a crash.
# Remove with: scripts\uninstall-autostart.ps1
#
# Keep this file ASCII-only: PowerShell 5.1 reads .ps1 as ANSI.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$supervise = Join-Path $root "scripts\supervise.ps1"
if (-not (Test-Path $supervise)) {
    Write-Error "supervisor not found: $supervise"
    exit 1
}

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $powershell)) {
    Write-Error "powershell.exe not found: $powershell"
    exit 1
}

$command = '"{0}" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{1}"' -f $powershell, $supervise
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $runKey -Force | Out-Null
Set-ItemProperty -Path $runKey -Name "DafeiyuQQBot" -Value $command

$oldShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "dafeiyu-qqbot.lnk"
if (Test-Path $oldShortcut) {
    Remove-Item -LiteralPath $oldShortcut -Force
    Write-Host "removed old startup shortcut: $oldShortcut"
}

Write-Host "autostart installed (registry Run, nothing written to C:)"
Write-Host "  key : HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DafeiyuQQBot"
Write-Host "  cmd : $command"
Write-Host ""
Write-Host "Takes effect at next logon. Start now: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\supervise.ps1"
