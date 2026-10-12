$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$startupPath = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupPath '生产管理看板_V0.2.0.lnk'
$scriptPath = Join-Path $PSScriptRoot 'start_dashboard_V0.2.0.ps1'
$windowsPowerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $windowsPowerShellPath
$shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "' + $scriptPath + '" -NoBrowser'
$shortcut.WorkingDirectory = $workspacePath
$shortcut.WindowStyle = 7
$shortcut.Description = '公司内网生产管理看板 V0.2.0 本机服务'
$shortcut.Save()
if (-not (Test-Path -LiteralPath $shortcutPath)) { throw '开机启动入口创建失败' }
Write-Output '已安装当前Windows用户登录后的看板服务自动启动入口。'
