# Remove autostart (HKCU Run value + legacy startup shortcut).
#
# Keep this file ASCII-only: PowerShell 5.1 reads .ps1 as ANSI.

$ErrorActionPreference = "Stop"

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$removed = $false
if (Test-Path $runKey) {
    $existing = Get-ItemProperty -Path $runKey -Name "DafeiyuQQBot" -ErrorAction SilentlyContinue
    if ($existing) {
        Remove-ItemProperty -Path $runKey -Name "DafeiyuQQBot"
        Write-Host "removed autostart value: $runKey\DafeiyuQQBot"
        $removed = $true
    }
}

$lnkPath = Join-Path ([Environment]::GetFolderPath("Startup")) "dafeiyu-qqbot.lnk"
if (Test-Path $lnkPath) {
    Remove-Item -LiteralPath $lnkPath -Force
    Write-Host "removed legacy shortcut: $lnkPath"
    $removed = $true
}

if (-not $removed) {
    Write-Host "nothing to remove"
}

Write-Host "project data untouched"
