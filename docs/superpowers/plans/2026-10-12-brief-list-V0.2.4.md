# V0.2.4 跟进清单摘要实施计划

**目标：** 跟进清单一句话扫读，完整说明在详情保留。

**架构：** 展示摘要独立保存在metadata；通过当前事项内容指纹避免显示旧结果，Store.snapshot按需提供list_summary；前端用安全文本节点显示，并限制两行。

- [x] 创建app/briefs.py：摘要指纹、最长80字、已完成优先当前范围、未编写时用下一步的第一短句；记录发生变化时不用旧摘要。
- [x] Store增加set_list_summaries映射保存和snapshot派生list_summary；不改items表里的完整summary与evidence。
- [x] web/app.js渲染list_summary，详情补完整事项说明；web/styles.css清单摘要两行，提示点击查看详情。资源版本参数更新0.2.4。
- [x] 本地现有19条摘要由子代理编写、主代理逐条复核；每日流程导入后用Store.set_list_summaries更新，以后新增事项也适用。
- [x] 隔离测试验证摘要不会覆盖原文、人工完成后旧摘要失效、新采集摘要变化失效；浏览器截图核对桌面和375px清单、点击详情和全量原文。迭代版本和文档后核验本任务服务身份，重启并验证health/实际业务摘要。
