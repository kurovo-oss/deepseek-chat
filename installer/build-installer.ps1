# Build the Windows installer. The NSIS compiler is project-local and never installed on C:.
# Usage: powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $root "build\installer"
$payload = Join-Path $buildRoot "payload"
$dist = Join-Path $root "dist"
$makensis = Join-Path $root "tools\nsis\makensis.exe"
if (-not (Test-Path $makensis)) {
    $makensis = Join-Path $root "tools\nsis\Bin\makensis.exe"
}
if (-not (Test-Path $makensis)) { throw "makensis.exe not found; install NSIS under tools\\nsis" }

if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Path $payload -Force | Out-Null
New-Item -ItemType Directory -Path $dist -Force | Out-Null

function Copy-Tree {
    param([string]$Source, [string]$Target, [string[]]$Exclude = @())
    if (-not (Test-Path $Source)) { throw "missing source: $Source" }
    $arguments = @($Source, $Target, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
    foreach ($item in $Exclude) {
        $arguments += "/XD"
        $arguments += $item
    }
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $Source (exit $LASTEXITCODE)" }
}

function Copy-File {
    param([string]$Name)
    $source = Join-Path $root $Name
    if (-not (Test-Path $source)) { throw "missing file: $Name" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $payload $Name) -Force
}

Copy-Tree (Join-Path $root "qqbot") (Join-Path $payload "qqbot") @("__pycache__", ".pytest_cache")
Copy-Tree (Join-Path $root "prompts") (Join-Path $payload "defaults\prompts")
Copy-Tree (Join-Path $root "scripts") (Join-Path $payload "scripts") @("__pycache__")
Copy-Tree (Join-Path $root "docs") (Join-Path $payload "docs")
if (Test-Path (Join-Path $root "assets")) {
    Copy-Tree (Join-Path $root "assets") (Join-Path $payload "assets")
}

$python = Join-Path $root "tools\python"
if (-not (Test-Path (Join-Path $python "python.exe"))) { throw "tools\\python is missing" }
Copy-Tree $python (Join-Path $payload "tools\python") @("__pycache__", ".pytest_cache")

$napcat = Get-ChildItem (Join-Path $root "tools") -Directory -Filter "NapCat-*" |
    Sort-Object Name -Descending |
    Select-Object -First 1
if ($null -eq $napcat) { throw "NapCat directory is missing under tools" }
Copy-Tree $napcat.FullName (Join-Path $payload "tools\$($napcat.Name)") @("__pycache__", ".pytest_cache")

$exeName = ([string][char]0x5927) + ([char]0x80A5) + ([char]0x9C7C) + ".exe"
foreach ($name in @(
    $exeName,
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.WinForms.dll",
    "WebView2Loader.dll",
    "README.md",
    "LICENSE",
    "SAFETY.md",
    "THIRD-PARTY-NOTICES.md",
    ".env.example"
)) {
    Copy-File $name
}

# NSIS FileWrite uses ANSI, so non-ASCII comments would become mojibake.
# The installer writes an ASCII defaults file with comments stripped; the
# readable Chinese template remains in .env.example.
$setupLines = @()
foreach ($line in [IO.File]::ReadAllLines((Join-Path $root ".env.example"), [Text.Encoding]::UTF8)) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0) { continue }
    if ($trimmed.StartsWith("#")) { continue }
    $setupLines += $line
}
[IO.File]::WriteAllLines((Join-Path $payload "setup.env"), $setupLines, [Text.Encoding]::ASCII)

Push-Location $PSScriptRoot
try {
    $log = Join-Path $buildRoot "makensis.log"
    & $makensis "/V1" "Dafeiyu.nsi" *> $log
    if ($LASTEXITCODE -ne 0) { throw "makensis failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

$output = Join-Path $dist "Dafeiyu-Setup-1.0.0.exe"
if (-not (Test-Path $output)) { throw "installer was not created: $output" }
$item = Get-Item $output
Write-Host ("built {0} ({1:N0} MB)" -f $item.FullName, ($item.Length / 1MB))
