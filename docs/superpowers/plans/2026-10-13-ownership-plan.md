# V0.2.10 跟进负责人实施计划

**目标：** 主动提出或推动事项的人可推定为跟进负责人，并能追溯及人工确认。

**实施方式：** 本会话顺序执行，无需另开任务。沿用Python/SQLite、原生JavaScript和本地Codex后台。

- [ ] 新建 app/ownership.py，统一来源核验、依据展示和人工指定规则。
- [ ] 修改 app/jobs.py 的严格输出合同、提示词与原消息回填，增加售后 owner。
- [ ] 修改 app/store.py、app/after_sales.py，保留人工覆盖，增加明确确认请求，普通同值保存不确认。
- [ ] 修改 web/app.js、web/index.html：短标记、详情依据、确认负责人按钮；不扩张清单长文。
- [ ] 修改 app/reports.py：HTML/PDF标明依据类型，CSV及JSON保留完整依据，历史报告隐藏期后判断。
- [ ] 新增 tests/test_ownership.py，使用虚构证据验证推定、分派、篡改拒绝、人工确认与导入保护。执行 vendor/python/python.exe -X utf8 -B -m unittest discover -s tests -q。
- [ ] 现有消息只在private内分析回补，不改人工状态、进展时间及来源采集时间；保存本地验收。
- [ ] 同步 VERSION、app版本、页面缓存参数、CHANGELOG、安装文档及V0.2.10入口。
- [ ] 构建完整微信语音包、核验隔离启动与内容清洁，提交白名单代码，使用 scripts/publish_github.py --push 发布。
- [ ] 上传v0.2.10对应完整包、SHA、组件及许可源码附件，回读远端私有可见性、main、VERSION、标签和全部资产校验值。
