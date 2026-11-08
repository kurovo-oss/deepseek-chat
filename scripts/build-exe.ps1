# Build the native Dafeiyu control panel (Dafeiyu.exe, Chinese display name) with the C# compiler shipped in Windows.
# The exe is a real WinForms window: it starts scripts\supervise.ps1 hidden and
# talks to the local control API with X-UI-Token. No browser is opened.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) {
    throw "csc.exe not found"
}

$sources = Get-ChildItem (Join-Path $PSScriptRoot "native") -Filter "*.cs" | ForEach-Object { $_.FullName }
if ($sources.Count -eq 0) { throw "no native sources found" }
$icon = Join-Path $root "qqbot\ui\assets\dafeiyu.ico"
$name = ([string][char]0x5927) + ([char]0x80A5) + ([char]0x9C7C) + ".exe"
$output = Join-Path $root $name

if (-not (Test-Path $icon)) {
    $python = Join-Path $root "tools\python\python.exe"
    if (Test-Path $python) {
        & $python (Join-Path $PSScriptRoot "make-icon.py")
    } else {
        throw "missing icon; run scripts\make-icon.py first"
    }
}

& $csc /nologo /target:winexe /codepage:65001 /optimize+ /platform:anycpu `
    "/win32icon:$icon" "/out:$output" `
    /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll `
    $sources

if ($LASTEXITCODE -ne 0) { throw "csc failed (exit $LASTEXITCODE)" }
$item = Get-Item $output
Write-Host ("built {0} ({1:N0} bytes)" -f $item.FullName, $item.Length)

