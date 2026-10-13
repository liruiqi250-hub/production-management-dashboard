# V0.2.5 直接下载实施计划

- [x] 前端导出改fetch/blob/a.download，处理文件名、下载中、错误、登录过期、对象URL释放；保留默认PDF及当前其它格式。
- [x] Server附件头补ASCII兼容文件名及UTF-8中文文件名，保持原有鉴权。
- [x] 更新VERSION/app.__version__/页面资源/CHANGELOG/README，新增V0.2.5启动入口。
- [x] 项目AGENTS.md保存以后每次版本更新并同步已指定GitHub的规则；发布器使用独立清洁发布checkout，白名单、秘密扫描、私有仓库与标签核验。
- [x] 在临时数据库及隔离端口检查点击下载实际PDF、页面不跳转、重复点击限制、失败和失效会话；70项检查通过，手机375px无横向溢出。
- [x] 核验本任务PID后重启到0.2.5，health实际返回ready和0.2.5。

发布验收流程：提交本机源码，运行发布器推送main和v0.2.5，实际回读远端版本、提交、标签及私有属性。实际发布结果保存在本机private/github_publication_V0.2.5.json；未成功核验时不宣称已同步。
