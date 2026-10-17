param()
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$pythonwPath = Join-Path $workspacePath 'vendor\python\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) { throw '缺少无控制台运行组件，请先安装完整便携包。' }
$entry = Join-Path $PSScriptRoot 'notification_deadline.py'
$taskName = 'Codex-ProductionDashboard-NotificationDeadline-' + $env:USERNAME
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and (@($existing.Actions).Count -ne 1 -or -not $existing.Actions[0].Arguments.Contains($entry))) {
  throw '同名任务属于其他目录，未覆盖。'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument ('-X utf8 -B "' + $entry + '"') -WorkingDirectory $workspacePath
$trigger = New-ScheduledTaskTrigger -Daily -At '16:50'
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description '16:50静默检查当天整理；未成功则仅提醒李睿琪，独立于Codex窗口。' -Force | Out-Null
Write-Output ('已安装16:50本人异常提醒检查：' + $taskName)
