param(
    [string]$Python = "H:\deepseek-chat\tools\gpt-sovits\venv39\Scripts\python.exe",
    [int]$Port = 9880
)

$ErrorActionPreference = "Stop"
$Root = "H:\deepseek-chat"
$Repo = Join-Path $Root "tools\gpt-sovits\GPT-SoVITS"
$Log = Join-Path $Root "tools\gpt-sovits\api.log"
$PidFile = Join-Path $Root "tools\gpt-sovits\api.pid"

if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    Write-Host "GPT-SoVITS already listening on $Port"
    exit 0
}
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}
if (-not (Test-Path (Join-Path $Repo "api_v2.py"))) {
    throw "GPT-SoVITS repo not found: $Repo"
}

$env:TMP = Join-Path $Root "tools\gpt-sovits\temp"
$env:TEMP = $env:TMP
$env:HF_HOME = Join-Path $Root "tools\gpt-sovits\cache\hf"
$env:TORCH_HOME = Join-Path $Root "tools\gpt-sovits\cache\torch"
$env:MODELSCOPE_CACHE = Join-Path $Root "tools\gpt-sovits\cache\modelscope"
$env:PATH = (Join-Path $Root "tools\ffmpeg") + ";" + $env:PATH
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null

$process = Start-Process -FilePath $Python `
    -ArgumentList @("api_v2.py", "-a", "127.0.0.1", "-p", "$Port", "-c", "GPT_SoVITS/configs/tts_infer.yaml") `
    -WorkingDirectory $Repo `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Log `
    -RedirectStandardError ($Log + ".err") `
    -PassThru
Set-Content -Encoding ascii -Path $PidFile -Value $process.Id
Write-Host "GPT-SoVITS started, pid=$($process.Id), log=$Log"
