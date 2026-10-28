@echo off
chcp 65001 >nul
rem 启动协议端 + 机器人（开机自启就用这个）
setlocal
cd /d "%~dp0.."

rem 协议端已经在跑就不要重复启动（6099 是它的 WebUI 端口）
netstat -ano | findstr /C:":6099" >nul 2>&1
if not errorlevel 1 (
  echo 协议端似乎已在运行，跳过启动。
  goto :bot
)

set "NAPCAT="
for /d %%D in ("tools\NapCat-*") do set "NAPCAT=%%~fD\launcher.bat"

if not defined NAPCAT goto :nonapcat
if not exist "%NAPCAT%" goto :nonapcat

echo 启动协议端: %NAPCAT%
start "NapCat" "%NAPCAT%"
echo 等待 20 秒让协议端完成登录...
timeout /t 20 /nobreak >nul
goto :bot

:nonapcat
echo 未找到 tools\NapCat-*\launcher.bat，跳过协议端。
echo 先运行 scripts\get-napcat.bat 下载。

:bot
call "%~dp0start-bot.bat"
