@echo off
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0..\..\check_environment.ps1" %*
pause
