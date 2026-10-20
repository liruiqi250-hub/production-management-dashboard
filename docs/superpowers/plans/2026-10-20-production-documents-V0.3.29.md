# 生产单自动建档、资料生成及预览 Implementation Plan

> For agentic workers: use subagent-driven-development in the current session. 用户已批准本次开发，缺项留空，不添加人工核对门槛。

**Goal:** 每日从已授权生产资料群的新生产单自动建档，在生产进度订单内展示详情并生成标签、钳工质检表、产品质检表，下载前预览真实打印页面。

**Architecture:** 本机确定性读取器保存原件、来源、产品明细、冲突说明，按内容哈希防重。三种生成器共用同一版本明细；质检模板仅写业务数据、结果签字空白。Excel经本机LibreOffice转PDF并逐页渲染PNG，受原登录权限保护。

**Tech Stack:** 现有Python/SQLite/原生JS/CSS，标准OOXML与openpyxl，本机LibreOffice和PDF渲染组件。

- [x] 新增 app/production_order_intake.py：按表头读取生产单、字段来源、数量单位及规格分配；新单自动建档、源哈希防重、同单新版不覆盖人工进度；tests/test_production_order_intake.py验证幂等与缺项。
- [x] 在 app/jobs.py 已有采集后接入生产资料群白名单附件建档，输出production_order_intake.json；原调度频率、账号、通知和AI分析范围保留。
- [x] 找到并净化本机两份标准模板，写入private/production_templates并记录来源SHA及清空位置；不入Git、不改原件。
- [x] 新增 app/production_documents.py：labels/fitter/product、版本历史、Excel/PDF/PNG、来源和输出哈希；tests/test_production_documents.py覆盖空白/数量/打印设置/错误状态。
- [x] app/server.py增加已登录详情、生成、文件及逐页预览接口；生成使用会话和CSRF，API令牌只读；导入仅后台固定来源。
- [x] web/production.js和styles.css增加内联详情、三个生成按钮、真实分页预览与Excel/PDF下载；原单缺字段显示空白，冲突仅说明，生成不改变工序/交付。
- [x] 用用户指定真实原单实际建档和试跑，覆盖多规格分配和混合单位，规格分配冲突的单体尺寸空白，跟单员空白，不修改源单。验证实际PDF全部源字段、特殊跨页和首末预览，原件SHA前后保持一致。
- [x] 新增HTTP权限及前端状态测试，运行项目Python/JS回归、Impeccable检测器，电脑/手机实际截图，修复发现的问题。
- [x] 更新VERSION/app.__version__/页面静态参数/CHANGELOG/启动入口到V0.3.29，保留V0.3.28可回退快照；核对PID及命令行后更新本机服务。
- [x] 更新已有每日自动化提示加入新单建档回执验收，保持15:45实际计划、原模型/项目/通知策略；无需新建重复任务。完成后仅询问是否上传GitHub，不自行发布。
