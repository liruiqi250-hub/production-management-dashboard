# 项目协作规则

- 全部用中文回复用户。
- 开发管理界面入口时，一并检查docs/management-ui-checklist.md中的数量、统计口径、清单、直达、刷新、空状态与多端配套，不让用户逐项补提；不得据此擅自更改业务判断或权限。
- 本项目每次软件更新均递增版本号，同步VERSION、app.__version__、页面版本与静态资源参数、CHANGELOG及当前启动入口。
- 按用户2026-10-19最新指示：每次更新先在本机完成、递增版本并验证，再询问是否上传GitHub；只有用户明确同意后才同步私有仓库 https://github.com/liruiqi250-hub/production-management-dashboard 、推送v<版本号>标签并构建上传完整运行包。未答复不视为同意，不自动上传，也不为每次本机小改动预先构建大运行包。此规则替代之前的长期自动上传授权。
- Git源码保存代码、说明、依赖锁文件与构建入口；按用户授权，通用运行依赖同时打包到对应GitHub Release，保留原组件许可、来源与SHA256。微信原文、媒体附件、业务数据库、密码、令牌、本机采集及分析文件必须保留本机，禁止提交或推送。
- 根工作区Git历史含旧的业务原文，不能把根分支直接推送到GitHub。使用scripts/publish_github.py --push，将当前已提交的代码及说明同步到runtime/github_publish中的独立清洁main分支，保留已发布的安全历史。不得强制推送或覆盖既有版本标签。
- 发布后实际核对远端私有可见性、main提交、VERSION和版本标签。若推送失败，保留已验证的本机更新，明确报告失败原因并继续处理，不声称已同步。
- 用户确认发布GitHub后，再用scripts/build_portable.py构建该版本Windows x64便携ZIP，按对应标签上传GitHub Releases并回读文件名、大小与校验值；不得把整套本机venv、Codex运行环境或浏览器用户目录直接打包。
- 对内网正在运行的服务，只能核对本项目PID及命令行后重启；保留已有用户密码、权限和人工结果。

- 从V0.2.7起发布完整微信/语音依赖包（build_portable.py --with-wechat），并上传SHA与许可要求的准确源码附件；接收者Codex先读INSTALL_FOR_CODEX.md。新电脑组件安装、私有绑定、指定群真实采集与定时任务分别验收，同事只读使用不重复部署采集。

- 2026-10-31 用户要求两个项目分仓：本项目仅发布生产看板；彩灯独立仓库为 https://github.com/liruiqi250-hub/lantern-management-dashboard 。PROJECT.json 固定 production，禁止从本工作区发布或构建彩灯包。本次拆分含发布已获明确授权，不构成后续自动上传授权。

- 2026-10-31 用户最新要求生产看板公开：production-management-dashboard 使用 public，PROJECT.json 声明 visibility=public，发布时核验实际公开属性；本条替代上文针对生产仓库的私有要求。彩灯仍独立私有。本次公开与 CMD 整理含发布已获授权，后续更新仍须逐次确认上传。
- 根目录仅保留固定名称的四个常用 CMD；版本入口放 scripts/launchers/current，旧兼容入口放 scripts/launchers/legacy，对移动入口修正相对路径并验证参数透传。旧 CMD 不代表旧版程序或回退副本。
