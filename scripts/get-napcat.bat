@echo off
rem 一键下载 NapCat（QQ 协议端）
rem 自动寻找可用的 Python：优先本机 DSH 运行时，其次 py 启动器，最后 PATH 上的 python。
rem 注意：Windows 自带的 python.exe 常是 Microsoft Store 空壳，必须验证它能真的跑。

setlocal
set "PYEXE="

for /d %%D in ("%USERPROFILE%\.dsh\dsh-runtimes\*\dependencies\python") do (
  if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
)
if not defined PYEXE for %%C in (py.exe) do if not "%%~$PATH:C"=="" set "PYEXE=%%~$PATH:C"
if not defined PYEXE for %%C in (python.exe) do if not "%%~$PATH:C"=="" set "PYEXE=%%~$PATH:C"
if not defined PYEXE goto :nopython

"%PYEXE%" --version >nul 2>&1
if errorlevel 1 goto :nopython

"%PYEXE%" "%~dp0get_napcat.py" %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%

:nopython
echo 没找到可用的 Python。
echo 请手动运行：
echo   "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" "%~dp0get_napcat.py"
echo.
pause
exit /b 1
