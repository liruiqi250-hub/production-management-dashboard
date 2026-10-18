# 钉钉登录 V0.3.22

本功能支持普通浏览器OAuth授权登录及钉钉工作台内自动登录。普通浏览器仍需点击使用钉钉登录；钉钉内打开时自动识别当前身份，沿用既有绑定进入本人事项。真实端内桥接须在实际钉钉客户端验收，不能以模拟结果代替。现有12小时看板会话不延长，平台身份接口所需联网仍须可用，看板本身继续仅在内网服务。

## 本机管理员配置

1. 创建企业内部应用并添加网页应用能力，移动/PC首页设置为本机当前内网看板网址。
2. 安全设置重定向URL准确配置为 `http://内网IP:端口/auth/dingtalk/callback`。当前实现只支持已核验内网HTTP部署，不会自动发布外网或调整网络隔离。建议由网络管理员安排固定内网地址，IP变更后同步更新两处配置。
3. 开通个人基础信息读取 `Contact.User.Read`。登录显式请求 `openid corpid Contact.User.Read`；用户另有用途的其它权限不更改、不由此登录功能调用。平台默认权限按后台规则保留。实际授权页若出现超出登录用途的授权范围，应停止并核验。
4. 密钥只由管理员保存在本机 `private/dingtalk/<业务数据库文件名去掉.sqlite3>.json`。恐龙版文件名为 `production_V0.2.0.json`。字段为 `enabled` 布尔值、`client_id`、`client_secret`、`corp_id`、`redirect_uri`。不要把实际值写入聊天、Git、报表、运行包或共享文档。
   `client_secret` 必须使用后台复制的完整密钥，不能使用带星号的页面预览；遮挡值或含空白的密钥会被视为无效配置。
5. 缺少/无效配置时登录入口隐藏，原账号可继续使用。配置完整后先备份本机数据库，核验项目PID后重启；先用本人进行真实扫码验证，再按批准范围发布给员工。

## 员工首次使用

点击“使用钉钉登录”，完成本人钉钉登录授权。未绑定时回到看板，用本人现有拼音账号和密码，勾选确认绑定，再点击“登录并绑定钉钉”。不勾选则只用原账号登录，不建立绑定。初始密码尚未修改的账号先按原流程改密，再绑定。

后续同一钉钉身份按企业ID及unionId哈希查找既有账号。保留办公室归属、角色及逐事项写权限，不接受钉钉昵称作为赋权依据、不自动创建本地账号。停用账号不能登录，角色变更后按当前数据库权限处理。

账号页显示本人绑定状态，可点击“解绑钉钉并退出”，解绑会吊销本人全部看板会话；原密码仍可重新登录。只读Codex令牌保持原有权限规则。账号遗失、绑定冲突或离职需由管理员核验，不覆盖现有身份绑定。

## 验收与实现边界

- 健康、登录、生产/售后数量及原事项权限分别核验。完成应用创建或看到二维码不等于真实登录成功。
- 一次性state与独立浏览器HttpOnly Lax cookie，10分钟有效；首次绑定临时cookie也为HttpOnly，有效期10分钟。状态和外部身份以哈希保存，原授权码和用户访问/刷新令牌不入库。
- 回调先事务消费state，再从固定官方HTTPS接口换取令牌与个人身份，校验返回企业ID；拒绝重放、不同浏览器、过期、企业不符和一对一绑定冲突。
- 只调用授权码接口与用户个人基础身份接口，不读取钉钉聊天，不调用手机号、支付宝、日历或企业全员通讯录接口。返回的其它字段不保存。
- 桌面扫码与手机授权需由实际使用人完成。便携包仅带通用代码；接收者没有私有配置时不能宣称已接通本企业。
- 授权失败时，本机 `private/dingtalk/<业务数据库文件名去掉.sqlite3>.last_failure.json` 只保留最近一次时间和受限错误标识，供管理员排查；不记录授权码、密钥、令牌、个人资料或接口响应原文，不向匿名页面返回诊断内容。

接口依据：[钉钉官方授权码教程](https://open-dingtalk.github.io/developerpedia/docs/develop/permission/token/browser/get_user_app_token_browser/)、[官方SDK授权码接口](https://github.com/alibabacloud-go/dingtalk/blob/master/oauth2_1_0/client.go)、[官方SDK个人身份接口](https://github.com/alibabacloud-go/dingtalk/blob/master/contact_1_0/client.go)。

## 工作台端内自动登录 V0.3.22

- 在公司钉钉工作台点击应用，页面先显示身份识别状态；默认根首页无需更换地址。只在钉钉UA中自动运行桥接，普通浏览器不加载SDK或自动跳转。UA本身不构成身份凭据。
- 固定企业应用token：POST `/v1.0/oauth2/{corpId}/token`；微应用免登码：`runtime.permission.requestAuthCode`；用户身份：POST `/topapi/v2/user/getuserinfo`。只取unionid，与原OAuth绑定共用同一企业和主体哈希，不额外读取人员详情、手机号或部门。不混用OAuth授权码与微应用免登码。
- GET `/api/auth/dingtalk/workbench/start` 生成5分钟一次nonce和独立HttpOnly Strict cookie；POST `/api/auth/dingtalk/workbench/login` 要求精确同源Origin、nonce和cookie，网络交换前事务消费，失败不重放。应用token仅在服务内存中短期缓存，授权码与返回个人资料不入日志或数据库。
- 成功后沿用原load/initLoginView：员工本人未结、管理全部，后端原逐事项权限照常检查。未绑定仅进入本人密码加明确勾选绑定页；被停用、必须改密和绑定冲突均按原规则。
- 识别失败、SDK或原生回调超时显示恢复提示并保留原登录入口。退出只停留当前页，不立即再次免登；重新打开工作台时再识别。企业额外已开权限和应用可见范围均不更改。
- SDK固定3.2.9，只打包需要的桥接，来源、完整性、许可见web/vendor及dependencies/dingtalk-jsapi-build.lock.json，重建见scripts/build_dingtalk_sdk.ps1。运行包接收电脑无需Node/npm。

V0.3.22可选日报推送：仅在本机明确配置 `capture_leader_route` 后，固定企业原生免登成功且主体哈希匹配原 `zengyue` 绑定时，保存该响应中曾悦本人的userid到私有消息路由。不读取其他成员通讯录，不改变登录结果和权限。详见 [日报推送](dingtalk-notifications.md)。

依据：[官方微应用免登示例](https://github.com/open-dingtalk/h5app-auth-demo)、[官方固定企业应用token协议](https://open-dingtalk.github.io/developerpedia/docs/develop/permission/single_to_multi/new_get_app_token/)。
