param()
$ErrorActionPreference = 'Stop'
$workspacePath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
. (Join-Path $PSScriptRoot 'check_environment.ps1')
$runtime = Resolve-DashboardPython -WorkspacePath $workspacePath
$pythonwPath = Join-Path (Split-Path -Parent $runtime.executable) 'pythonw.exe'
$entry = Join-Path $PSScriptRoot 'run_cloudflared.py'
$tokenPath = Join-Path $workspacePath 'private/remote_access/cloudflared.token'
if (-not (Test-Path -LiteralPath $pythonwPath) -or -not (Test-Path -LiteralPath $tokenPath)) { throw '请先完成本机隧道凭据配置；不能使用示例令牌。' }
& $runtime.executable -X utf8 -B -c "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from app.store import Store; from app.remote_access import RemoteGateway; sys.exit(0 if RemoteGateway(Store(Path(sys.argv[1]))).origin else 2)" $workspacePath
if ($LASTEXITCODE -ne 0) { throw '公网网关尚未启用或配置无效，未安装任务。' }
$taskName = 'Codex-ProductionDashboard-Tunnel-' + $env:USERNAME
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and (@($existing.Actions).Count -ne 1 -or -not $existing.Actions[0].Arguments.Contains($entry))) { throw '同名任务不属于本项目，未覆盖。' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument ('-X utf8 -B "' + $entry + '"') -WorkingDirectory $workspacePath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 -Hidden
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description '生产看板专用Cloudflare通道：用户登录后静默连接，仅转发已配置的看板服务。' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output ('已安装专用静默通道任务：' + $taskName)
