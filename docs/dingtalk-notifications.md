# 钉钉日报推送 V0.3.21

已确认业务范围：钉钉“办公群”收到生产与售后简明日报及看板链接；曾悦本人收到重点、逾期和需协调事项专报。每天15:30启动的既有集中整理任务成功归档后自动推送，目标16:30前完成。周末照常运行；三天优先计时继续沿用工厂规则，仅扣已核验放假日期。

## 处理与权限

- `app.jobs` 完成微信采集、理解手册复核、台账导入及日报周报归档后，调用固定推送流程。采集或归档未完成不发送旧日报。
- 简报使用已登记状态、办公室跟进人和短摘要；不复制微信原文，不重新推定负责人或完成。生产、售后分开计数后合计，完成项和售后待并案不计待处理；阶段认可不等于整单完工。各办公室人员包括零事项都显示。
- 曾悦专报按现有三天优先、明确已过期的日期、摘要中明确协调要求列示，不生成未经依据支持的交期或领导决定。
- 固定链接打开后仍执行原有钉钉绑定及看板权限。推送不会增加任何员工的修改权限。
- 15:30以前的手动整理不群发；当天15:30以后首次成功整理各通道发送一次。16:30后完成的消息标注“延迟更新”，不声称准时。重跑任务不会再次发送同一天同目标同类型消息。

## 本机配置

凭据沿用本机 `private/dingtalk/production_V0.2.0.json`，不上传云端源码或运行包。消息配置为 `private/dingtalk/notifications.json`：

```json
{
  "enabled": false,
  "capture_leader_route": true,
  "robot_code": "本公司应用的Client-ID",
  "group": {"name": "办公群", "verified": false, "open_conversation_id": "已核验群会话ID"},
  "leader": {"username": "zengyue"}
}
```

新安装默认没有配置，不会发送消息。企业机器人和企业内机器人发送消息权限需由本公司管理员启用，并发布应用；将该机器人添加到已核验的办公群。群ID必须核对，不以近似名称替代。

曾悦的消息用户ID优先从其本人重新打开工作台的原生免登响应取得：只有固定企业、原绑定主体哈希和 `zengyue` 账号一致且账号有效时，才写 `private/dingtalk/zengyue_notification_route.json`。不读取手机号或全量通讯录、不保存原始unionId。应用未配置 `capture_leader_route` 时不保存该路由；解除绑定或停用后通知校验失效。路由仍需在启用真实推送前验证。

只有办公群ID、群名和曾悦收件身份全部核验后才能将 `enabled` 及群 `verified` 设为true。消息正文、收件标识和回执只保存在本机 `private/notifications/outbox.sqlite3`。

## 运行与记录

```powershell
vendor/python/python.exe -X utf8 -B -m app.dingtalk_notifications
vendor/python/python.exe -X utf8 -B -m app.dingtalk_notifications --preview
vendor/python/python.exe -X utf8 -B -m app.dingtalk_notifications --test
vendor/python/python.exe -X utf8 -B -m app.dingtalk_notifications --retry-failed
```

默认只查看安全状态列表，不返回正文或收件标识。preview只在本机生成预览，不发送，预览不是当天整理已完成的证明。test向两个固定通道各发一条“推送连通测试”，不含生产数据，独立防重。retry-failed只允许今天completed的任务且只重试明确拒绝的通道；发送结果不确定时禁止自动重发。

状态：pending待发送；sending已进入发送调用；accepted平台已接受（不等于已读）；rejected明确拒绝、可核对后显式重试；unknown网络或回执不确定、先核验实际消息。进程在sending时中断也不会自动重复。每个通道独立记录，一方成功不会因另一方失败而重发。通知故障不会回滚已更新业务台账，任务完成状态附带通知结果。

## 上线验收

分别核验机器人配置已发布、消息权限已启用、办公群与曾悦身份、API接受、实际收到及链接打开。首次部署不自动发送旧日报。次日15:30任务是否自动启动、16:30前是否完成及消息是否实际到达要分别记录；办公室关机、离线或采集失败时不能保证准点。

官方协议依据：[钉钉企业应用机器人](https://open.dingtalk.com/document/orgapp/the-creation-and-installation-of-the-application-robot-in-the)、[官方机器人SDK请求与响应](https://github.com/alibabacloud-go/dingtalk/blob/master/robot_1_0/client.go)。使用固定 `groupMessages/send` 与 `oToMessages/batchSend` 接口，不使用个人身份模拟发消息或已退役的群Webhook回复地址。

## 卡片、PDF与本人异常提醒

V0.3.21使用官方sampleActionCard2模板：摘要、打开看板、下载日报PDF。报告发送时冻结在本机private/notifications/reports；PDF按钮经原登录页进入有账号鉴权的下载接口，不公开业务文件，不在链接中放令牌。正文少于500字，详情和证据在看板；预览明确资料截至日期，不冒称今天整理成功。

本机notifications.json可增加capture_operator_route=true、operator_alerts_enabled=true，只保存原绑定liruiqi本人的消息路由；账号停用或绑定变化后拒绝发送。`vendor/python/python.exe -X utf8 -B -m app.notification_cards --preview-to-me`仅向李睿琪发送办公群/曾悦两种卡片预览，各一次。本人确认样式后才设置presentation为card及cards_approved=true；未确认时原每日文字推送保留。

15:30后整理失败或partial由后台即时提醒本人，不附原文、错误详情或凭据。`scripts/install_notification_check.ps1`为维护电脑安装每日16:50的pythonw静默检查任务，独立于Codex窗口；若没有当天15:30后completed记录，覆盖没启动、仍运行和中断，提醒本人。即时失败和截止提醒共用每日防重键；平台接受不等于已读，结果不明不自动重发。17:00后补跑明确延迟；电脑关机、未登录、断网时不能保证17:00前送达。普通只读同事不安装该任务。

`vendor/python/python.exe -X utf8 -B -m app.notification_cards --check-deadline`可手动检查，16:50前不发截止提醒。日常收件范围仍为办公群及曾悦，liruiqi只收预览及异常；原事项权限不变。
