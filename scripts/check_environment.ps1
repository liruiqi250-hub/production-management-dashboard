param([Alias('PythonPath')][string]$EnvironmentPythonPath = '')

function Test-DashboardPython([string]$Executable, [string[]]$PrefixArguments = @()) {
  if (-not $Executable -or $Executable -match '(?i)[\\/]WindowsApps[\\/]' -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
  $probe = "import sys,json,sqlite3,ssl,hashlib; assert sys.version_info >= (3,12); hashlib.scrypt(b'probe',salt=b'probe',n=16,r=1,p=1,dklen=16); print(json.dumps({'executable':sys.executable,'version':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,'ssl':ssl.OPENSSL_VERSION,'scrypt':True}))"
  try {
    $result = & $Executable @PrefixArguments -I -X utf8 -c $probe 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $details = ($result -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($details.executable -match '(?i)[\\/]WindowsApps[\\/]' -or -not (Test-Path -LiteralPath $details.executable -PathType Leaf)) { return $null }
    return $details
  } catch { return $null }
}

function Resolve-DashboardPython([string]$WorkspacePath, [string]$PythonPath = '') {
  $candidates = @((Join-Path $WorkspacePath 'vendor\python\python.exe'))
  if ($PythonPath) { $candidates += $PythonPath }
  # 系统py负责选择真实解释器，不将WindowsApps的商店占位入口作为Python。
  $launcher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  foreach ($candidate in $candidates) {
    $details = Test-DashboardPython $candidate
    if ($details) { return $details }
  }
  if ($PythonPath) { throw '指定的Python不可用：需要Python 3.12或更高版本，且sqlite3、ssl和hashlib.scrypt实际运行正常。' }
  if ($launcher) {
    $details = Test-DashboardPython -Executable $launcher.Source -PrefixArguments @('-3')
    if ($details) { return $details }
  }
  foreach ($name in @('python.exe', 'python3.exe')) {
    foreach ($command in @(Get-Command $name -All -ErrorAction SilentlyContinue)) {
      $details = Test-DashboardPython $command.Source
      if ($details) { return $details }
    }
  }
  # 兼容现有办公室机器；便携包和正常系统Python优先。
  $codexPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
  $details = Test-DashboardPython $codexPython
  if ($details) { return $details }
  throw '未找到可用Python。请解压完整Windows运行包（vendor\python），或安装Python 3.12及以上，或使用-PythonPath指定解释器。sqlite3、ssl和hashlib.scrypt均必须可用。'
}

function Get-DashboardLanAddress {
  try {
    $adapters = @(Get-NetAdapter -ErrorAction Stop | Where-Object { $_.Status -eq 'Up' -and $_.HardwareInterface })
    $choices = @()
    foreach ($route in @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop)) {
      if ($adapters.Count -gt 0 -and -not ($adapters | Where-Object { $_.ifIndex -eq $route.InterfaceIndex })) { continue }
      $interface = Get-NetIPInterface -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue | Select-Object -First 1
      foreach ($address in @(Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue)) {
        if ($address.AddressState -ne 'Preferred' -or $address.IPAddress -notmatch '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)') { continue }
        $choices += [pscustomobject]@{ Address = $address.IPAddress; Metric = [int]$route.RouteMetric + [int]$interface.InterfaceMetric }
      }
    }
    return ($choices | Sort-Object Metric | Select-Object -First 1 -ExpandProperty Address)
  } catch { return $null }
}

function Get-DashboardPdfBrowser([string]$WorkspacePath) {
  $browserPaths = @((Join-Path $WorkspacePath 'vendor\chromium\chrome-headless-shell.exe'))
  if (${env:ProgramFiles(x86)}) { $browserPaths += Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe' }
  if ($env:ProgramFiles) { $browserPaths += Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe' }
  if ($env:LOCALAPPDATA) { $browserPaths += Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe' }
  foreach ($browser in $browserPaths) { if (Test-Path -LiteralPath $browser -PathType Leaf) { return $browser } }
  return $null
}

if ($MyInvocation.InvocationName -ne '.') {
  $ErrorActionPreference = 'Stop'
  try {
    $workspacePath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
    $runtime = Resolve-DashboardPython -WorkspacePath $workspacePath -PythonPath $EnvironmentPythonPath
    Write-Output "Python：$($runtime.version)；$($runtime.executable)"
    Write-Output "SQLite：$($runtime.sqlite)；hashlib.scrypt：通过；SSL：$($runtime.ssl)"
    $browser = Get-DashboardPdfBrowser $workspacePath
    if ($browser) { Write-Output "PDF浏览器：$browser" } else { Write-Output 'PDF浏览器：未找到；请使用完整运行包，或安装Microsoft Edge。HTML/CSV导出仍可用。' }
    $lan = Get-DashboardLanAddress
    if ($lan) { Write-Output "内网地址：$lan" } else { Write-Output '内网地址：暂未识别，本机127.0.0.1仍可用。' }
    if (Test-Path -LiteralPath (Join-Path $workspacePath 'private\wechat_binding.json') -PathType Leaf) {
      Write-Output '微信群采集：已存在本机绑定资料（未读取内容）；微信登录及授权状态需在本机确认。'
    } else {
      Write-Output '微信群采集：未配置本机授权绑定；运行包不包含微信数据、密钥及绑定资料。'
    }
    Write-Output '运行环境检查完成。此检查未启动服务、修改账号或读取聊天内容。'
  } catch {
    Write-Error $_.Exception.Message
    exit 1
  }
}
