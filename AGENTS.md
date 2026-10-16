# 项目协作规则

- 全部用中文回复用户。
- 开发管理界面入口时，一并检查docs/management-ui-checklist.md中的数量、统计口径、清单、直达、刷新、空状态与多端配套，不让用户逐项补提；不得据此擅自更改业务判断或权限。
- 本项目每次软件更新均递增版本号，同步VERSION、app.__version__、页面版本与静态资源参数、CHANGELOG及当前启动入口。
- 用户已长期授权：每次更新完成并验证后，自动同步到GitHub私有仓库 https://github.com/liruiqi250-hub/production-management-dashboard ，同时推送对应v<版本号>标签。不需要每次重新询问是否上传。
- Git源码保存代码、说明、依赖锁文件与构建入口；按用户授权，通用运行依赖同时打包到对应GitHub Release，保留原组件许可、来源与SHA256。微信原文、媒体附件、业务数据库、密码、令牌、本机采集及分析文件必须保留本机，禁止提交或推送。
- 根工作区Git历史含旧的业务原文，不能把根分支直接推送到GitHub。使用scripts/publish_github.py --push，将当前已提交的代码及说明同步到runtime/github_publish中的独立清洁main分支，保留已发布的安全历史。不得强制推送或覆盖既有版本标签。
- 发布后实际核对远端私有可见性、main提交、VERSION和版本标签。若推送失败，保留已验证的本机更新，明确报告失败原因并继续处理，不声称已同步。
- 每次软件发布同时用scripts/build_portable.py构建该版本Windows x64便携ZIP，按对应标签上传GitHub Releases并回读文件名、大小与校验值；不得把整套本机venv、Codex运行环境或浏览器用户目录直接打包。
- 对内网正在运行的服务，只能核对本项目PID及命令行后重启；保留已有用户密码、权限和人工结果。

- 从V0.2.7起发布完整微信/语音依赖包（build_portable.py --with-wechat），并上传SHA与许可要求的准确源码附件；接收者Codex先读INSTALL_FOR_CODEX.md。新电脑组件安装、私有绑定、指定群真实采集与定时任务分别验收，同事只读使用不重复部署采集。
