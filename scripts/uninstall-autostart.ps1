# Remove the autostart shortcut created by install-autostart.ps1

$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "dafeiyu-qqbot.lnk"

if (Test-Path $lnkPath) {
    Remove-Item -LiteralPath $lnkPath -Force
    Write-Host "Autostart removed: $lnkPath"
} else {
    Write-Host "Nothing to remove (no shortcut at $lnkPath)"
}

Write-Host "Project data is untouched; only the startup shortcut was deleted."
