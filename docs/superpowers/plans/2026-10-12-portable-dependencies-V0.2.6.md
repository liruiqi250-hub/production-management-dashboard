# V0.2.6 依赖打包实施计划

目标：在现有GitHub私有仓库提供可解压运行的Windows x64看板与PDF依赖包，并保留可重建的依赖来源。

1. 审计app/scripts/automation的运行路径、标准库与外部依赖，核对公开组件原始许可；通用组件缓存于runtime/dependency_cache，私有材料不参与构建。
2. 新建dependencies/windows-x64.lock.json、scripts/build_portable.py及归档边界检查。固定Python及PDF组件URL/版本/SHA256，完整保留许可；Python使用相对_pth且不启用外部site环境。
3. 改scripts/start_dashboard_V0.2.0.ps1，新增环境检查入口及V0.2.6启动入口。通过随包Python、端口参数及工作区标识在隔离目录验证，不操作正式服务。
4. app/pdf_export.py优先随包PDF组件，app/server.py提供工作区标识；测试组件选择，实际在便携包里生成中文PDF。
5. 微信依赖另提供锁文件与安装脚本，默认只检查，显式安装才写vendor/wechat；本机账号绑定保存在private。采集脚本按滚动7天授权范围运行，新增日期边界验证。
6. 迭代VERSION/app.__version__/静态资源/README/CHANGELOG/AGENTS；运行测试及ZIP逐文件校验，使用含空格中文新路径实测服务和下载。
7. 核对正式PID后部署0.2.6；提交源码，通过独立清洁发布库推送main及v0.2.6；上传便携ZIP和SHA256到同标签GitHub Release并核验下载内容。

验收数据、压缩包与发布记录只存本机analysis、outputs/V0.2.6及private。源码提交不包含本机生产材料；远端最终核验记录保存在private/github_publication_V0.2.6.json。
