# Restart the QQ protocol side (NapCat) when the QQ kernel session dies.
#
# Symptom seen on 2026-10-28: NapCat WebUI and get_status still report the
# account online, get_login_info still returns the nickname, but sending fails
# with result 1006514 "network error" and get_friend_list returns an empty
# array. The only recovery is restarting QQ, which needs Administrator.
#
# This script self-elevates, kills ONLY the NapCat process tree (your own QQ
# client is left alone), then starts the launcher again.

$ErrorActionPreference = "Continue"

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ChildProcessId {
    param([int]$ParentId)
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        $child.ProcessId
        Get-ChildProcessId -ParentId $child.ProcessId
    }
}

if (-not (Test-Admin)) {
    Write-Host "Requesting Administrator rights (a UAC prompt will appear)..."
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"')
    )
    exit 0
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$napcatDir = Get-ChildItem (Join-Path $projectRoot "tools") -Directory -Filter "NapCat-*" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $napcatDir) {
    Write-Host "Cannot find tools\NapCat-* under $projectRoot"
    exit 1
}

$launcher = Join-Path $napcatDir.FullName "launcher.bat"
if (-not (Test-Path $launcher)) {
    Write-Host "Cannot find $launcher"
    exit 1
}

$boots = @(Get-CimInstance Win32_Process -Filter "Name='NapCatWinBootMain.exe'" -ErrorAction SilentlyContinue)
if ($boots.Count -eq 0) {
    Write-Host "NapCat is not running; starting it."
} else {
    foreach ($boot in $boots) {
        $ids = @(Get-ChildProcessId -ParentId $boot.ProcessId)
        [array]::Reverse($ids)
        foreach ($procId in $ids) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $boot.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("Stopped NapCat tree rooted at " + $boot.ProcessId)
    }
    Start-Sleep -Seconds 3
}

Start-Process -FilePath $launcher -WorkingDirectory $napcatDir.FullName
Write-Host "Launcher started. Waiting for QQ to come back..."

$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $alive = @(Get-CimInstance Win32_Process -Filter "Name='NapCatWinBootMain.exe'" -ErrorAction SilentlyContinue)
    if ($alive.Count -gt 0) {
        Start-Sleep -Seconds 5
        Write-Host "NapCat is running again. If QQ asks for a QR code, scan it with the bot account."
        exit 0
    }
}

Write-Host "NapCat did not come back within 60 seconds. Check the launcher window."
exit 1
