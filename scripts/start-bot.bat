@echo off
chcp 65001 >nul
rem 只启动机器人本体（协议端需另行启动）
setlocal
cd /d "%~dp0.."

set "PYEXE="
if exist "tools\python\python.exe" set "PYEXE=%CD%\tools\python\python.exe"
if not defined PYEXE (
  for /d %%D in ("%USERPROFILE%\.dsh\dsh-runtimes\*\dependencies\python") do (
    if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
  )
)
if not defined PYEXE for %%C in (py.exe) do if not "%%~$PATH:C"=="" set "PYEXE=%%~$PATH:C"
if not defined PYEXE goto :nopython

echo 解释器: %PYEXE%
echo 工作目录: %CD%
"%PYEXE%" -m qqbot
set "CODE=%ERRORLEVEL%"
echo.
echo 机器人已退出，退出码 %CODE%
exit /b %CODE%

:nopython
echo 找不到可用的 Python。
echo 期望位置: %CD%\tools\python\python.exe
exit /b 1
