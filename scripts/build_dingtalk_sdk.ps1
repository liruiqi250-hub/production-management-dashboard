# 固定npm依赖重建端内免登桥接；仅维护电脑需要Node，接收电脑无需构建。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $projectRoot 'runtime/dingtalk_sdk_build'
New-Item -ItemType Directory -Force $buildRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'dependencies/dingtalk-jsapi-build.lock.json') -Destination (Join-Path $buildRoot 'package-lock.json')
$packageJson = '{"dependencies":{"dingtalk-jsapi":"3.2.9","esbuild":"0.25.12"}}'
[IO.File]::WriteAllText((Join-Path $buildRoot 'package.json'), $packageJson)
& npm.cmd ci --prefix $buildRoot --ignore-scripts --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw '固定依赖安装失败' }
$entry = @"
require('dingtalk-jsapi/entry/union');
const requestAuthCode = require('dingtalk-jsapi/api/runtime/permission/requestAuthCode').default;
window.DinoDingTalk = {requestAuthCode: corpId => requestAuthCode({corpId})};
"@
[IO.File]::WriteAllText((Join-Path $buildRoot 'entry.js'), $entry)
& (Join-Path $buildRoot 'node_modules/.bin/esbuild.cmd') (Join-Path $buildRoot 'entry.js') --bundle --minify --format=iife --target=es2017 --legal-comments=inline "--outfile=$(Join-Path $projectRoot 'web/vendor/dingtalk-workbench.js')"
if ($LASTEXITCODE -ne 0) { throw 'SDK打包失败' }
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $projectRoot 'web/vendor/dingtalk-workbench.js')
