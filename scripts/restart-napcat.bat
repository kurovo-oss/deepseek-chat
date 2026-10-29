@echo off
REM 重启协议端（不需要管理员权限）。
REM 强制结束 QQ 会让登录凭证失效，所以这里改走快速登录；凭证失效时
REM NapCat 会打印二维码，扫码即可。QR 会同步到 data\qq-login-qr.png。
cd /d "%~dp0.."
taskkill /F /IM QQ.exe >nul 2>&1
taskkill /F /IM NapCatWinBootMain.exe >nul 2>&1
timeout /t 3 /nobreak >nul
cd /d "%~dp0..\tools\NapCat-v4.18.41"
start "" /min cmd /c "launcher-user.bat 2680359191 > console.log 2>&1"
echo 协议端已启动，30 秒后检查 3001 端口。
echo 二维码（如果需要扫码）：data\qq-login-qr.png