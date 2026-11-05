# Supervise the bot process: if it exits, wait 15s and start it again.
# Also boots the NapCat protocol side once (when port 6099 is not listening).
#
# Start : powershell -NoProfile -WindowStyle Hidden -File scripts\supervise.ps1
# Stop  : scripts\stop-bot.ps1
#
# Keep this file ASCII-only: PowerShell 5.1 reads .ps1 as ANSI.

$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root "logs\supervise.log"
$python = Join-Path $root "tools\python\python.exe"

# Single instance: two supervisors would spawn two bots.
$mutex = New-Object System.Threading.Mutex($false, "DafeiyuQQBotSupervisor")
$isNew = $false
try {
    $isNew = $mutex.WaitOne(0, $false)
} catch [System.Threading.AbandonedMutexException] {
    $isNew = $true
}

function Say([string]$message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $log -Value ("{0} {1}" -f $stamp, $message) -Encoding UTF8
}

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "qqbot" }
}

function Test-Napcat {
    $listener = Get-NetTCPConnection -LocalPort 6099 -State Listen -ErrorAction SilentlyContinue
    return [bool]$listener
}

if (-not $isNew) {
    Say "another supervisor is running, exit"
    exit 0
}

if (-not (Test-Path $python)) {
    Say "python not found: $python"
    exit 1
}

$already = @(Get-BotProcesses)
if ($already.Count -gt 0) {
    Say ("bot already running: {0}, exit" -f ($already.ProcessId -join ","))
    exit 0
}

Say "supervisor started"

if (-not (Test-Napcat)) {
    $napcat = Get-ChildItem (Join-Path $root "tools") -Directory -Filter "NapCat-*" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($napcat) {
        $launcher = Join-Path $napcat.FullName "launcher.bat"
        Say "starting protocol side: $launcher"
        Start-Process -FilePath $launcher -WorkingDirectory $napcat.FullName -WindowStyle Hidden
        for ($i = 0; $i -lt 30 -and -not (Test-Napcat); $i++) {
            Start-Sleep -Seconds 2
        }
        $state = if (Test-Napcat) { "listening" } else { "down, start bot anyway" }
        Say ("port 6099: {0}" -f $state)
    } else {
        Say "tools\NapCat-* not found, skip protocol side"
    }
}

while ($true) {
    Say "starting qqbot"
    $process = Start-Process -FilePath $python -ArgumentList @("-m", "qqbot") `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $process.WaitForExit()
    Say ("qqbot exited with {0}, restart in 15s" -f $process.ExitCode)
    Start-Sleep -Seconds 15
}
