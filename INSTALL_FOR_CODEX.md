# 交给接收电脑 Codex 的安装说明（V0.2.7）

请用中文操作和报告结果。先阅读本文件、AGENTS.md和`docs/Codex安装验收.md`。用户已授权安装该项目；优先使用包内组件，无需另装Python、Node、pip或临时下载语音模型。

## 先确定使用方式

如果用户只是查看办公室看板或让自己的Codex总结生产数据：使用办公室当前内网网址登录，并由本人生成自己的只读Codex令牌，按README的API接口读取。无需在每位同事电脑上建立空台账或重复采集微信；令牌只存当地私有位置，不发给别人或上传GitHub。

如果本机作为看板服务器或迁移接替办公室电脑：下载私有仓库最新Release的`production-dashboard-V0.2.7-full-windows-x64.zip`及同名`.sha256`。GitHub自带的Source code ZIP不包含运行组件。核验下载包SHA256后解压至本机可写的中文/空格目录也可使用；不要解压到微信数据目录。对应Release的第三方源码附件用于许可与重建，正常运行无需解压该源码附件。

## 安装与启动

1. 确认Windows 10/11 x64、磁盘空间及包内SHA256SUMS.json。运行包内`vendor/python/python.exe -X utf8 scripts/doctor_installation.py --json --require-wechat`，读取JSON和退出码。首次缺少本机微信绑定是“需配置”，不能写成组件损坏或采集已成功。
2. 运行`检查完整安装.cmd`，再运行`启动生产看板_V0.2.7.cmd`。也可通过`powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts/start_dashboard_V0.2.0.ps1 -NoBrowser`启动。若Windows标记下载文件为阻止，核验来源与哈希后只解除此解压目录内文件的阻止，不更改全局执行策略。
3. 服务健康检查应返回V0.2.7且workspace_id与本目录对应；核对启动器显示的内网地址，不沿用原电脑固定IP。新副本为空台账，初始账号文件由本机首次启动生成，首次登录后本人改密。更新已有部署必须先备份本机private及业务材料，不重置用户密码、不覆盖人工结果。
4. 实际登录、手机布局、日报及周报下载分别验收。其它设备用当前办公室内网地址连接；若确因防火墙被拦，确认当前受信任网络和具体端口后仅配置该端口规则，不关闭防火墙、不开放公网。

## 配置本机微信采集

只有在用户授权本机进行微信采集时才做此段。微信客户端由用户安装并登录其授权账号，确认所需14群和最近7天记录在本机可读。不要复制原作者的微信账号、登录态、数据库、私有绑定或旧任务。

使用`配置微信采集.cmd`或`vendor/python/python.exe -X utf8 scripts/configure_wechat_binding.py --help`查看参数。核验本机微信容器目录、账号子目录和稳定用户名后写入本机`private/wechat_binding.json`，不要猜值。保存后先执行`--check`，再用包内`vendor/wechat/python/python.exe`执行`collect_scoped_wechat_V0.1.3.py --phase probe`的元数据核验。probe不读取群消息正文，不等于真实导出已通过。

在项目根目录执行完整核验命令，每次使用新的输出目录：

```powershell
$probeOutput = Join-Path (Get-Location) ('acquisition/codex_probe_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
& .\vendor\wechat\python\python.exe -I -X utf8 -B .\scripts\collect_scoped_wechat_V0.1.3.py --phase probe --group-indices '1,2,3,4,5,6,7,8,9,10,11,12,13,14' --out $probeOutput
```

此步骤核验本项目明确授权的14群；若当地账号不具备对应授权或群不匹配，停止真实导出并记录待确认项，不能改为全群搜索和采集。

需要实际导出时，使用限定采集入口`scripts/daily_collect_V0.2.0.ps1`或上述collector的`--phase export`，只允许源码GROUPS列出的14群和滚动最近7天。不得直接运行第三方导出器进行全库或全群采集。采集原文、语音、图片、视频、文件仅保留本机acquisition/private，禁止上传GitHub。语音在本机通过随包模型识别；未能下载或识别的媒体必须保留为缺口，不能冒称已审核或据此关闭问题。

微信版本与本机可读状态仍会影响采集；失败应保留上一版台账，记录实际错误。不要把账号绑定文件存在、依赖导入成功或静音推理通过写成真实微信采集通过。

## 每日整理与验收报告

若本机接替办公室每日整理，按`automation/每日生产整理_V0.2.0.md`建立当前用户明确授权的Codex任务：中国时间15:30开始，目标16:30前完成。安装包不携带原作者的Codex任务ID或账号；先确认现有办公室任务是否仍负责采集，避免双重任务。桌面微信需处于可读取状态，电脑须开机。未来某次任务是否准时成功只能以该次运行结果验收。

安装结束请分别报告：包和组件校验、服务与报表、本机微信绑定、指定群真实采集、分析导入、每日任务设置。未完成的项如实注明原因，不合并成笼统“全部安装成功”。任何凭据、私有数据及真实账号标识均不写入提交、截图或公共日志。
