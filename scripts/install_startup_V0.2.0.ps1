param([ValidateRange(1, 65535)][int]$Port = 8769)
$ErrorActionPreference = 'Stop'
$workspacePath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
. (Join-Path $PSScriptRoot 'check_environment.ps1')
$runtime = Resolve-DashboardPython -WorkspacePath $workspacePath
$pythonwPath = Join-Path (Split-Path -Parent $runtime.executable) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) { throw '未找到无控制台pythonw.exe，请先完成便携运行组件安装。' }
$checkPath = Join-Path $PSScriptRoot 'ensure_dashboard_running.py'
if (-not (Test-Path -LiteralPath $checkPath)) { throw '缺少后台健康检查入口。' }
$taskName = 'Codex-ProductionDashboard-' + $env:USERNAME
if ($Port -ne 8769) { $taskName += '-' + $Port }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
  $owned = @($existing.Actions | Where-Object {
    $_.Arguments -and ($_.Arguments.Contains($checkPath) -or
      $_.Arguments.Contains((Join-Path $PSScriptRoot 'start_dashboard_V0.2.0.ps1')))
  })
  if ($owned.Count -ne 1 -or @($existing.Actions).Count -ne 1) {
    throw '同名计划任务属于其他目录或配置，未覆盖。请维护人员核验后处理。'
  }
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument ('-X utf8 -B "' + $checkPath + '" --port ' + $Port) -WorkingDirectory $workspacePath
$login = New-ScheduledTaskTrigger -AtLogOn -User $identity
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($login, $periodic) -Principal $principal -Settings $settings -Description '生产看板：当前用户登录后启动，每5分钟无控制台检查；健康时不启动PowerShell。' -Force | Out-Null
# 只移除本项目旧版快捷方式，避免升级后两种自启重复运行。
$startupPaths = @([Environment]::GetFolderPath('Startup'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup')) | Select-Object -Unique
$shortcutShell = New-Object -ComObject WScript.Shell
foreach ($startupPath in $startupPaths) {
  if (-not $startupPath) { continue }
  $shortcutPath = Join-Path $startupPath '生产管理看板_V0.2.0.lnk'
  if (Test-Path -LiteralPath $shortcutPath) {
    $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
    if ($shortcut.Arguments -and $shortcut.Arguments.Contains((Join-Path $PSScriptRoot 'start_dashboard_V0.2.0.ps1'))) {
      Remove-Item -LiteralPath $shortcutPath
    }
  }
}
Start-ScheduledTask -TaskName $taskName
Write-Output ('已安装当前Windows用户登录自启及每5分钟静默检查：' + $taskName)
Write-Output '看板网页服务不依赖Codex窗口；关机、睡眠或未登录时不可保证访问。微信与AI整理须另行验收。'
