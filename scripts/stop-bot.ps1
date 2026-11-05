# Stop the supervisor and the bot process. The protocol side (NapCat) is left
# alone: QQ stays logged in and the bot can be started again at any time.
#
# Keep this file ASCII-only: PowerShell 5.1 reads .ps1 as ANSI.

$ErrorActionPreference = "Continue"

$killed = 0
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "supervise\.ps1" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("stopped supervisor: {0}" -f $_.ProcessId)
        $killed++
    }

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "qqbot" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("stopped bot: {0}" -f $_.ProcessId)
        $killed++
    }

if ($killed -eq 0) {
    Write-Host "no supervisor or bot process found"
}
