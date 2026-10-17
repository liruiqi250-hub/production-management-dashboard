# 免费固定入口与办公室通道（V0.3.19）

此功能默认关闭。需要在公司批准的 Cloudflare 账号中单独部署 Worker、Tunnel 与 VPC Service；下载便携包不会继承原电脑的隧道或钉钉凭据。

## 结构及范围

手机/电脑 → HTTPS `production-dashboard.<账户子域>.workers.dev` → Worker 的 `DASHBOARD` VPC Service 绑定 → 专用 Cloudflare Tunnel → 办公室 `127.0.0.1:8769`。

使用 VPC Service 限定目标 IP 与端口，不创建整个公司网段的 VPC Network 访问。无需变更自有域名、DNS、路由器端口映射或在员工手机上安装 VPN。办公室电脑需要开机、联网并登录 Windows。服务仍由本机运行，页面、API 和报表下载经过 Cloudflare；不迁移业务数据库和本地微信媒体。

## 运维配置

1. 为项目创建专用远程管理 Tunnel；保留 tunnel UUID，令牌只保存到 `private/remote_access/cloudflared.token`。不要将后台显示的安装命令或令牌发到聊天、Git 或日志。
2. 创建 HTTP VPC Service：连接此 Tunnel，目标 IPv4 为 `127.0.0.1`，HTTP 端口为 `8769`。不要添加其他网段。隧道必须以 QUIC 连通；HTTP/2 回退不能替代 Workers VPC 的 QUIC 要求。
3. 独立 Worker 源码为 `automation/cloudflare-gateway/worker.mjs`，绑定名 `DASHBOARD` 指向上述 VPC Service；普通变量 `PUBLIC_ORIGIN` 为实际固定 HTTPS 网址（不带尾斜杠）。
4. 用密码学随机数生成至少32字节专用网关密钥；Cloudflare 侧配置加密 secret `GATEWAY_SECRET`。本机 `private/remote_access/production_V0.2.0.json` 设置 `enabled: true`、`public_origin` 与 `gateway_secret`。密钥必须一致；钉钉 Client Secret 不上传 Cloudflare。
5. 核验本项目 PID、运行命令行后重启看板。连接器来自随包 `vendor/cloudflared`，固定版本、SHA256 和 Apache-2.0 许可记录在 `dependencies/cloudflared.lock.json`。由 `scripts/install_remote_gateway.ps1` 安装当前 Windows 用户的登录自启任务，使用 pythonw 和无控制台子进程，避免黑框。独立任务不覆盖原内网看板任务。
6. 钉钉企业内部应用的手机/PC工作台入口设置为上述 HTTPS 网址；OAuth 回调添加 `<网址>/auth/dingtalk/callback`。保留原有绑定、授权范围和登录权限。真实工作台免登、未绑定账号流程及回调必须分别验证。

连接器只记录 warn 级运行日志到本机 `private/remote_access/connector.log`，启动时超过2MB轮换上一份，禁止 debug。指标仅监听 `127.0.0.1:20242`；`/ready` 用于确认连接状态。连接失败由计划任务一分钟后重试，正常运行由 cloudflared 自己处理重连。

## 访问校验与验收

Worker 将固定服务作为唯一代理目标，剥离客户端伪造的 Host/转发/网关声明头，再注入专用网关凭据。看板仅对回环连接、准确专用密钥与配置的公网来源接受 HTTPS 声明，并为 Cookie 添加 Secure。公网写入必须带准确 HTTPS Origin，原 CSRF、本人绑定、账号状态及事项权限继续执行。员工可看他人但只能修改自己负责事项；管理者按既有账号权限处理。

上线前需验证：隧道 QUIC `/ready`、公网未登录不能读业务、钉钉实际身份、员工与管理者视图、越权拒绝、日报/周报直接下载、手机布局，以及家中 Wi-Fi 或移动数据实际连通。办公室本机测试不能代替外网手机验收。

Workers 与 VPC 的免费政策和可用性需以 Cloudflare 当前官方说明和账户实际套餐为准。VPC 当前为 Beta；不自动启用付费计划。workers.dev 可达性须用实际员工网络验证。

## 停用与回退

先停止本项目 `Codex-ProductionDashboard-Tunnel-<Windows用户名>` 任务，并核对 `connector.pid` 对应进程确属本项目 cloudflared 后停止；禁止按进程名批量结束。将本机 remote_access 配置 `enabled` 改为 false，并核对本项目看板 PID 后重启。内网地址继续使用。外部 Worker 可先移除入口路由以停用，删除云资源前另行确认。回退不覆盖业务数据库、现有密码、人员权限或钉钉绑定。

官方依据：[VPC Service](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/)、[私有 API 示例](https://developers.cloudflare.com/workers-vpc/examples/private-api/)、[VPC 计费](https://developers.cloudflare.com/workers-vpc/platform/pricing/)、[隧道 QUIC](https://developers.cloudflare.com/workers-vpc/configuration/tunnel/)。
