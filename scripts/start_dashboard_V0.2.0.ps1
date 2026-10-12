param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$pythonPath = 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '未找到已验证的Python运行环境。请联系维护人员恢复运行环境。' }
$lanAddress = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -eq '以太网' -and $_.IPAddress -like '192.168.*' } | Select-Object -First 1 -ExpandProperty IPAddress
if (-not $lanAddress) { throw '未找到公司以太网IPv4地址。请先连接公司内网。' }
$softwareVersion = (Get-Content -LiteralPath (Join-Path $workspacePath 'VERSION') -Raw).Trim()
$localUrl = 'http://127.0.0.1:8769'
$alreadyRunning = $false
try { $health = Invoke-RestMethod -Uri "$localUrl/health" -TimeoutSec 2; $alreadyRunning = $health.version -eq $softwareVersion } catch { }
if (-not $alreadyRunning) {
  $privatePath = Join-Path $workspacePath 'private'
  New-Item -ItemType Directory -Path $privatePath -Force | Out-Null
  $process = Start-Process -FilePath $pythonPath -ArgumentList @('-X','utf8','-B','-m','app.server','--host','0.0.0.0','--port','8769','--lan-ip',$lanAddress) -WorkingDirectory $workspacePath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $privatePath 'server_V0.2.0.log') -RedirectStandardError (Join-Path $privatePath 'server_error_V0.2.0.log') -PassThru
  $process.Id | Set-Content -LiteralPath (Join-Path $privatePath 'server_V0.2.0.pid') -Encoding UTF8
  for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 300
    try { $health = Invoke-RestMethod -Uri "$localUrl/health" -TimeoutSec 2; if ($health.version -eq $softwareVersion) { $alreadyRunning = $true; break } } catch { }
    if ($process.HasExited) { throw '看板启动失败，请查看private目录的server_error_V0.2.0.log。' }
  }
  if (-not $alreadyRunning) { throw '看板未在预期时间内就绪，请查看本机日志。' }
}
Write-Output "生产管理看板 V${softwareVersion}：本机 $localUrl；公司内网 http://${lanAddress}:8769"
if (-not $NoBrowser) { Start-Process "$localUrl/" -WindowStyle Hidden }
