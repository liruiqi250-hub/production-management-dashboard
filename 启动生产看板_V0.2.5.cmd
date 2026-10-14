@echo off
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0scripts\start_dashboard_V0.2.0.ps1"
if errorlevel 1 pause
