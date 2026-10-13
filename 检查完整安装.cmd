@echo off
chcp 65001 >nul
setlocal
if not exist "%~dp0vendor\python\python.exe" (
  echo 未找到包内 Python。请先解压完整运行包，再运行本入口。
  exit /b 1
)
"%~dp0vendor\python\python.exe" -I -X utf8 -B "%~dp0scripts\doctor_installation.py" --require-wechat %*
exit /b %errorlevel%
