# Install autostart: create a shortcut in the user's Startup folder
# that launches scripts\start-all.bat at logon.
#
# Footprint on C: : one .lnk file (~1 KB). All actual data stays on the
# project drive.

$ErrorActionPreference = "Stop"

$target = Join-Path $PSScriptRoot "start-all.bat"
if (-not (Test-Path $target)) {
    Write-Error "start-all.bat not found next to this script: $target"
    exit 1
}

$startup = [Environment]::GetFolderPath("Startup")
if (-not $startup) {
    Write-Error "Cannot locate the Startup folder for the current user."
    exit 1
}

$lnkPath = Join-Path $startup "dafeiyu-qqbot.lnk"
$projectRoot = Split-Path -Parent $PSScriptRoot

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Start Dafeiyu QQ bot"
$shortcut.Save()

Write-Host "Autostart installed."
Write-Host "  shortcut : $lnkPath"
Write-Host "  target   : $target"
Write-Host "  workdir  : $projectRoot"
Write-Host ""
Write-Host "It will run at next logon. To start now, double-click scripts\start-all.bat"
