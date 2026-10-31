# 接收电脑 Codex 安装说明 V0.3.34

> V0.3.34 为本机修复版本，尚未上传 GitHub、未构建完整运行包；下方同版本部署步骤仅在用户确认发布且对应 Release 实际提供附件后适用。源码、组件及实际业务接入分别验收。

更新发布规则：本机完成、递增版本并验证后，先询问用户是否上传GitHub；未获明确同意，不运行推送、不推标签，也不预先构建完整运行包。本机版本可能高于GitHub最近发布版，以实际已发布Release为准。

V0.3.34支持本机管理员明确持续授权后的自动阶段复核。已有部署保留private/context_review_policy.json及沟通资料，更新不重置授权和人工结果；新电脑没有授权文件时默认只生成候选，不能擅自用旧电脑或他人名义启用。策略结构与验收见docs/communication-profile.md。后台每轮保存stage_review_plans.json及stage_review_result.json，须结合真实台账验收。

如本机已有 `private/communication_profiles_V1.0.0.json`，更新时保留。每轮整理会重新校验并读取，任务目录中的 `communication_profile_receipt.json` 可核验实际加载的版本、SHA和范围。该文件包含本机私有人员沟通案例，不包含在GitHub源码和运行包内；新电脑默认没有，不代表安装缺件。正式迁移需在用户授权下单独本机复制并重新核对办公室映射，详见 `docs/communication-profile.md`。

## 先按用户目的选择系统

- 恐龙生产管理：下载 `production-dashboard-V0.3.34-full-windows-x64.zip`，运行 `启动生产看板_V0.3.34.cmd`，默认8769端口，按下方恐龙流程验收。
- 彩灯已迁入独立仓库：https://github.com/liruiqi250-hub/lantern-management-dashboard 。
- 两套账号、数据库、群清单和邮箱来源互相独立。不要把恐龙指定群绑定带入彩灯，也不要用彩灯人员配置覆盖恐龙办公室名单。

## 恐龙生产管理部署

请用中文操作和报告结果。先阅读本文件、AGENTS.md和`docs/Codex安装验收.md`。用户已授权安装该项目；优先使用包内组件，无需另装Python、Node、pip或临时下载语音模型。

## 先确定使用方式

如果用户只是查看办公室看板或让自己的Codex总结生产数据：使用办公室当前内网网址登录，并由本人生成自己的只读Codex令牌，按README的API接口读取。无需在每位同事电脑上建立空台账或重复采集微信；令牌只存当地私有位置，不发给别人或上传GitHub。

如果本机作为看板服务器或迁移接替办公室电脑：下载私有仓库最新Release的`production-dashboard-V0.3.34-full-windows-x64.zip`及同名`.sha256`。GitHub自带的Source code ZIP不包含运行组件。核验下载包SHA256后解压至本机可写的中文/空格目录也可使用；不要解压到微信数据目录。对应Release的第三方源码附件用于许可与重建，正常运行无需解压该源码附件。

## 安装与启动

1. 确认Windows 10/11 x64、磁盘空间及包内SHA256SUMS.json。运行包内`vendor/python/python.exe -X utf8 scripts/doctor_installation.py --json --require-wechat`，读取JSON和退出码。首次缺少本机微信绑定是“需配置”，不能写成组件损坏或采集已成功。
2. 运行`检查完整安装.cmd`，再运行`启动生产看板_V0.3.34.cmd`。也可通过`powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts/start_dashboard_V0.2.0.ps1 -NoBrowser`启动。若Windows标记下载文件为阻止，核验来源与哈希后只解除此解压目录内文件的阻止，不更改全局执行策略。
3. 服务健康接口的version字段应为 `0.3.34`，且workspace_id与本目录对应；核对启动器显示的内网地址，不沿用原电脑固定IP。新副本为空台账，初始账号文件由本机首次启动生成，首次登录后本人改密。更新已有部署必须先备份本机private及业务材料，不重置用户密码、不覆盖人工结果。
4. 实际登录、手机布局、日报及周报下载分别验收。其它设备用当前办公室内网地址连接；若确因防火墙被拦，确认当前受信任网络和具体端口后仅配置该端口规则，不关闭防火墙、不开放公网。

## 恐龙生产资料、模板与打印验收

生产进度支持生产单搜索、原单详情、标签、钳工质检表、产品质检表以及下载前实际打印预览。读取已授权生产资料群的XLSX附件后按源SHA防重建档；不要求用户逐项审批才生成。未知字段（包括未填写的跟单员）、无法明确分配的逐件尺寸、实测结果、检验结论和签字均留空；数量/规格等冲突保留原文并在详情说明，不猜值、不自动填合格，也不据此修改已有人工工序或交付状态。

完整生产运行包按 `dependencies/production-documents.lock.json` 包含 openpyxl、et-xmlfile、pypdf、pypdfium2 和 Pillow及其许可证与摘要，不依赖Codex自身的Python环境。可用包内解释器执行以下只读导入检查；这一步仅确认库可导入，不代表真实模板、版式和打印已通过：

```powershell
& .\vendor\python\python.exe -X utf8 -B -c "import openpyxl, pypdf, pypdfium2, PIL; print('production document libraries ready')"
```

- 标签由程序生成。两类质检表还需要当前公司正式空白模板，本机分别放在 `private/production_templates/fitter.xlsx`、`private/production_templates/product.xlsx`；如有 `manifest.json`，摘要必须与所用模板一致。模板与本机清单不随源码或运行包发布。新电脑由用户提供或另行明确授权后单独本机导入，不从旧电脑整包复制private，不将旧客户、勾选结论或签字当空白模板。
- 实际Excel到PDF转换还需要目标服务器另行安装LibreOffice；便携ZIP不含LibreOffice安装程序。没有LibreOffice时保留已生成的Excel并显示预览不可用，不能将组件导入成功称为PDF预览成功。需用真实授权生产单验证三类Excel、PDF和逐页PNG中的中文、尺寸、份数、分页及空白签字区，再提供下载。
- 浏览器“打印 / 云打印”先准备本批次的实际打印页面，由用户在系统对话框选择当前设备已配置的打印机或云打印服务。每批最多80页，份数在系统对话框确认。云打印客户端、登录及设备接入由当地用户配置；此入口没有自动接入云端打印API，也不能据对话框结束认定已出纸。
- “由本机打印”通过运行看板的服务器发送至代码限定的固定队列 `KONICA MINOLTA bizhub C226 PCL (192.168.10.199) UPD`。名称中的IP不是自动发现的实际端口目标。新电脑先只读核对该队列、驱动及真实端口，由维护人员按当地明确授权配置，不照搬原电脑地址，不修改默认打印机来迁就软件。
- 固定队列打印还需LibreOffice安装目录内的 `python.exe` 与UNO组件。固定本机配置位于 `private/production_printing.json`，不随包迁移；无配置默认禁用。只有明确启用本机打印并核验固定队列后，才在当地私有配置中设置 `schema: 1`、`enabled: true` 和上述精确 `printer_name`。网页不接受客户端传入任意队列、驱动、命令或文件路径。查看账号可提交打印，个人API令牌不能提交；1至5份，每批页数乘份数最多250。
- 首先用合成PDF执行 `scripts/print_production_pdf.py` 的 `--dry-run` 载入/页数核验，配合本机受控管道和清单，确认没有调用打印；不能为了验收安装而擅自向实体或云设备出纸。用户明确提出真实打印后才提交，并分别记录系统接受与实际出纸结果。任务结果未知时先检查Windows队列，不自动重印；服务重启也不会自动重放正在提交的任务。

原生产单在 `private/production_order_sources`，可再生的Excel/PDF/PNG在 `private/production_document_outputs`，生成批次历史在业务数据库中。缓存连续7天未使用后由服务启动及每6小时维护清理；成功预览、下载或打印延长使用时间，列表刷新不延长，服务关闭时顺延。生成中、正在读取或活动打印任务关联批次跳过；未知文件、人工修改、链接及路径/摘要异常保留并记录原因。清理后可以按当前原单与模板重新生成，原单、模板、账号、台账和生成历史不删，用户自己下载的副本不由系统清理。清理回执位于 `private/production_document_cleanup/last.json`。

## 配置本机微信采集

集中整理功能提供“立即整理”：服务器电脑需安装并登录Codex（本包不复制Codex安装或认证文件）。核验 `codex login status`；管理账号可启动，其他授权账号只看状态。后台使用已登录Codex执行AI分析，语音识别使用包内本地模型；不要将“原始文件留本机”误写成“AI推理也全部离线”。同事只读使用仍不需要重复采集。

后台继承本机模型选择，日常整理的推理强度最高为high，不修改用户Codex配置。采集程序把已核验消息直接提供给分析器，分析器不需要桌面文件工具或其它聊天的工具管道；时间、发言人和证据原文由服务端按定位回填，AI候选仍待人工复核。

集中整理固定读取14个生产群和大洋安装售后群、云艺安装售后群、金耐安装售后群最近7天。额外群权限不能由页面任意输入。新电脑逐项核验这17群；无法唯一匹配时保留正式台账并报告失败。任务状态存private/jobs，严禁上传运行目录。

生产/售后3天无实质处理记录自动优先：用户工厂周末也上班，因此周末计入，仅扣2026年国家放假日期。跨未配置年份会提示补日历。历史售后第一次接入才启动跟踪，不能用历史沟通日期伪造最近进展；同一事项重新导入不重新计时。

只有在用户授权本机进行微信采集时才做此段。微信客户端由用户安装并登录其授权账号，确认所需14个生产群及3个售后群和最近7天记录在本机可读。不要复制原作者的微信账号、登录态、数据库、私有绑定或旧任务。

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

若本机接替办公室每日整理，按`automation/每日集中整理.md`配置当前用户明确授权的既有Codex任务：中国时间15:45开始，目标16:30前完成；迁移时先核对已有调度，不另建重复任务。安装包不携带原作者的Codex任务ID或账号；先确认现有办公室任务是否仍负责采集，避免双重任务。桌面微信需处于可读取状态，电脑须开机。未来某次任务是否准时成功只能以该次运行结果验收。

安装结束请分别报告：包和组件校验、服务与报表、本机微信绑定、指定群真实采集、分析导入、每日任务设置。未完成的项如实注明原因，不合并成笼统“全部安装成功”。任何凭据、私有数据及真实账号标识均不写入提交、截图或公共日志。

V0.3.34的办公室跟进人由办公室成员主动承接消息或明确交办推断，并保留owner_basis来源理由。自动推定待确认，人工指定与完成结果保留；owner不是实际执行人。详情可单独确认负责人，同事Codex读取时必须保留推定/人工确认区别。

V0.3.34纠正规则：owner只允许办公室名单内人员。业务员、客户和其他发言人只是沟通来源；从办公室主动承接及明确交办判断，没有依据留空待分派。昵称须精确核对，不能由包含姓名的字符串猜身份。默认办公室名单为李睿琪、张英、银小芹、王星、郭逸、张利伟；本机可在private/office_staff.json配置names列表和aliases对象，私有配置不随包迁移。


## 彩灯项目独立部署

彩灯源码、运行包与历史归档位于 https://github.com/liruiqi250-hub/lantern-management-dashboard ，请读取该仓库的 INSTALL_FOR_CODEX.md。本仓库不提供彩灯安装入口。
