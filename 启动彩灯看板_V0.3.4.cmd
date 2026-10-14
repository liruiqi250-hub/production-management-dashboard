@echo off
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0scripts\start_lantern.ps1" %*
if errorlevel 1 pause
