# 两个看板独立仓库迁移计划

**目标：** 生产看板保留 production-management-dashboard；彩灯看板迁到私有 lantern-management-dashboard，各自拥有默认项目、启动入口、版本和发布记录。

**执行：** 当前会话顺序执行。仅从独立清洁发布库提取源码，不上传生产工作区历史。保留旧标签供回退。

- [ ] 保存远端 Release 元数据和附件，逐个校验 SHA256；创建彩灯私有仓库。
- [ ] 从彩灯 v0.3.0～v0.3.4 标签提取白名单源码，建立独立历史和同名标签。
- [ ] 发布器读取 PROJECT.json，锁定仓库和打包用途；文件选择剔除另一套产品的启动入口与页面。
- [ ] 生产版本升至 V0.3.34，彩灯版本升至 V0.3.5；同步说明、版本和资源参数，保存回退源码。
- [ ] 对发布器及打包防串用进行回归；用临时空数据目录核验两套 HTTP 健康接口和登录页。
- [ ] 原样迁移彩灯历史 Release 附件，包括既有加密归档（不解密、不读取恢复密钥）；回读校验后清理旧仓库彩灯 Release，保留原标签。
- [ ] 构建两套完整包，上传各自仓库并回读文件大小、摘要、main、标签与私有属性。
- [ ] 更新原本机彩灯归档指针和迁移记录，不恢复或启动已归档业务服务。

**涉及文件：** scripts/publish_github.py、scripts/build_portable.py、tests/test_publish_github.py、PROJECT.json、VERSION、app/__init__.py、两套首页及当前启动入口、README.md、INSTALL_FOR_CODEX.md、AGENTS.md、CHANGELOG.md。

**验收命令：** python -m unittest tests.test_publish_github tests.test_portable_bundle -v；独立彩灯源码运行 tests.test_lantern*；各发布库通过 gh api 和 git ls-remote 回读。
