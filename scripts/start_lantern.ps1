param(
  [switch]$NoBrowser,
  [string]$PythonPath = '',
  [string]$LanAddress = '',
  [ValidateRange(1, 65535)][int]$Port = 8770
)
$ErrorActionPreference = 'Stop'
$workspacePath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
. (Join-Path $PSScriptRoot 'check_environment.ps1')
$runtime = Resolve-DashboardPython -WorkspacePath $workspacePath -PythonPath $PythonPath
$pythonExecutable = $runtime.executable
$softwareVersion = (Get-Content -LiteralPath (Join-Path $workspacePath 'VERSION') -Raw).Trim()

if ($LanAddress) {
  $ip = $null
  if (-not [Net.IPAddress]::TryParse($LanAddress, [ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'LanAddress必须是有效的IPv4地址。'
  }
} else {
  $LanAddress = Get-DashboardLanAddress
}
$bindAddress = '0.0.0.0'
if (-not $LanAddress) { $LanAddress = '127.0.0.1' }
if ($LanAddress -eq '127.0.0.1') { $bindAddress = '127.0.0.1' }
$localUrl = "http://127.0.0.1:${Port}"
$hash = [Security.Cryptography.SHA256]::Create()
try {
  $workspaceId = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($workspacePath.ToLowerInvariant())))).Replace('-', '').ToLowerInvariant()
} finally { $hash.Dispose() }
$privatePath = Join-Path $workspacePath 'private'
$logSuffix = "lantern_${Port}"
$pidPath = Join-Path $privatePath "server_${logSuffix}.pid"

function Test-ProjectProcess([int]$ProcessId) {
  $projectProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if (-not $projectProcess -or -not $projectProcess.CommandLine) { return $false }
  $command = $projectProcess.CommandLine
  return ($command -match '(?:^|\s)-m\s+app\.lantern_server(?:\s|$)' -and
    $command -match "(?:^|\s)--port\s+${Port}(?:\s|$)" -and
    $command -match "(?:^|\s)--workspace-id\s+${workspaceId}(?:\s|$)")
}
function Get-ListenerIds {
  try {
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {
    # Get-NetTCPConnection在无监听时也抛错；用系统netstat再次核验。
    $listenerIds = @()
    foreach ($line in (& "$env:SystemRoot\System32\netstat.exe" -ano -p tcp)) {
      if ($line -match "^\s*TCP\s+\S+:${Port}\s+\S+\s+LISTENING\s+(\d+)\s*$") { $listenerIds += [int]$Matches[1] }
    }
    return @($listenerIds | Select-Object -Unique)
  }
}
function Test-ProjectHealth {
  try {
    $health = Invoke-RestMethod -Uri "$localUrl/health" -TimeoutSec 2
    return ($health.status -eq 'ready' -and $health.department -eq 'lantern' -and $health.version -eq $softwareVersion -and $health.workspace_id -eq $workspaceId)
  } catch { return $false }
}

$listeners = @(Get-ListenerIds)
$alreadyRunning = $false
if ($listeners.Count -gt 0) {
  $savedProcessId = 0
  if (Test-Path -LiteralPath $pidPath) {
    [void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$savedProcessId)
  }
  if ($savedProcessId -gt 0 -and $listeners.Count -eq 1 -and $listeners[0] -eq $savedProcessId -and (Test-ProjectProcess $savedProcessId)) {
    $alreadyRunning = Test-ProjectHealth
    if (-not $alreadyRunning) { throw "本项目端口${Port}已运行其他版本或尚未就绪；请由维护人员核验该进程后重启。现有进程和PID记录未改动。" }
  } else {
    throw "端口${Port}已被占用，无法确认是本项目当前服务；请维护人员核验，或使用-Port指定空闲端口。未停止任何进程，也未覆盖PID记录。"
  }
}
if (-not $alreadyRunning) {
  New-Item -ItemType Directory -Path $privatePath -Force | Out-Null
  $arguments = @('-X', 'utf8', '-B', '-m', 'app.lantern_server', '--host', $bindAddress, '--port', "$Port", '--lan-ip', $LanAddress, '--workspace-id', $workspaceId)
  $process = Start-Process -FilePath $pythonExecutable -ArgumentList $arguments -WorkingDirectory $workspacePath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $privatePath "server_${logSuffix}.log") -RedirectStandardError (Join-Path $privatePath "server_error_${logSuffix}.log") -PassThru
  for ($attempt = 0; $attempt -lt 50; $attempt++) {
    Start-Sleep -Milliseconds 300
    $process.Refresh()
    if ($process.HasExited) { throw "看板启动失败，请查看private目录的server_error_${logSuffix}.log。" }
    if ((Test-ProjectProcess $process.Id) -and (Test-ProjectHealth)) {
      $process.Id | Set-Content -LiteralPath $pidPath -Encoding UTF8
      $alreadyRunning = $true
      break
    }
  }
  if (-not $alreadyRunning) { throw '看板未在预期时间内就绪，未覆盖原PID记录；请维护人员查看本机日志及新启动进程。' }
}
Write-Output "彩灯生产管理看板 V${softwareVersion}：本机 $localUrl"
if ($LanAddress -eq '127.0.0.1') {
  Write-Output '暂未识别到可用的公司内网地址；本机仍可使用。连接内网后可重新启动或使用-LanAddress指定地址。'
} else {
  Write-Output "公司内网：http://${LanAddress}:${Port}（同一内网的授权账号访问）"
}
if (-not $NoBrowser) { Start-Process "$localUrl/" -WindowStyle Hidden }
