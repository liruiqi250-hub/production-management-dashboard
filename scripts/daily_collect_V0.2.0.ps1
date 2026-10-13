param()
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$pythonPath = $env:DINO_WECHAT_PYTHON
if (-not $pythonPath) {
  $candidates = @(
    (Join-Path $workspacePath 'vendor\wechat\python\python.exe'),
    (Join-Path $workspacePath 'vendor\wechat\venv\Scripts\python.exe')
  )
  $pythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $pythonPath -or -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw '未安装微信采集环境，请使用完整便携包并按INSTALL_FOR_CODEX.md配置。' }
$bundledFfmpeg = Join-Path $workspacePath 'vendor\wechat\ffmpeg\bin\ffmpeg.exe'
if (Test-Path -LiteralPath $bundledFfmpeg -PathType Leaf) { $env:IMAGEIO_FFMPEG_EXE = $bundledFfmpeg }
$collectorPath = Join-Path $PSScriptRoot 'collect_scoped_wechat_V0.1.3.py'
$chinaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
$chinaNow = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $chinaTimeZone)
$startDate = $chinaNow.Date.AddDays(-6).ToString('yyyy-MM-dd')
$endDate = $chinaNow.Date.AddDays(1).ToString('yyyy-MM-dd')
$runName = $chinaNow.ToString('yyyy-MM-dd_HH-mm-ss')
$runPath = Join-Path $workspacePath "acquisition\V0.2.0\daily\$runName"
$privatePath = Join-Path $workspacePath 'private'
New-Item -ItemType Directory -Path $privatePath -Force | Out-Null
$lockPath = Join-Path $privatePath 'daily_collect.lock'
$lockStream = $null
try {
  $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
  $logPath = Join-Path $privatePath "collection_$runName.log"
  & $pythonPath -X utf8 -B $collectorPath --phase export --group-indices 1,2,3,4,5,6,7,8,9,10,11,12,13,14 --start $startDate --end-exclusive $endDate --media --transcribe-voices --out $runPath *> $logPath
  if ($LASTEXITCODE -ne 0) { throw "采集失败，上一版台账保留；查看 $logPath" }
  $manifestPath = Join-Path $runPath 'collection_manifest_V0.1.3.json'
  $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($manifest.status -ne 'exported' -or $manifest.export_results.Count -ne 14 -or -not $manifest.sensitive_cache_cleanup) { throw '采集未完整完成或缓存清理未通过；保留上一版台账，不更新成功时间。' }
  $manifestPath | Set-Content -LiteralPath (Join-Path $privatePath 'latest_collection_manifest.txt') -Encoding UTF8
  Write-Output $manifestPath
} finally {
  if ($lockStream) { $lockStream.Dispose() }
}
