# 生成资料清理与打印 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 生产进度增加生产单关键词搜索，将生成资料改为7天未使用自动清理，并增加浏览器/云打印和本机打印队列。

**Architecture:** ProductionDocuments负责批次文件、锁和后台清理；ProductionPrinting负责幂等持久队列及固定本机打印机；HTTP层鉴权、下载锁与打印页独立接入，前端沿用同一预览。

**Tech Stack:** Python sqlite3/threading、现有openpyxl/PDFium、Windows打印驱动、LibreOffice Python UNO、原生JS/CSS。

---

### Task 0: 生产单搜索

- [x] 搜索单号、标题、业务员、客户、跟单、来源和产品；多关键词取交集，与订单下拉共同生效。
- [x] 完整保留命中订单，提供数量、空状态、清空、刷新保留、退出清空和直达配套；20项前端测试及HTTP客户字段测试通过。
- [x] 电脑和手机实际验收，手机无横向溢出。

### Task 1: 批次保留与维护线程

**Files:** `app/production_documents.py`、`tests/test_production_document_retention.py`

- [x] 实现并验证 `hold(output_id)`、`cleanup_expired(now=None,retention_days=7,dry_run=False)`、`start_maintenance()`、`stop_maintenance()`；`protected_output_ids`回调默认空集合。
- [x] 文件成功使用触碰last_used_at，列表刷新不触碰；清理后抛ArtifactsExpired供HTTP410。只删批次内已登记且摘要相符的xlsx/pdf/png，保留未知及已改文件。
- [x] 用 `vendor/python/python.exe -m unittest tests.test_production_document_retention -v` 验证期限、保护、线程及路径边界。

### Task 2: 持久本机打印队列

**Files:** `app/production_printing.py`、`scripts/print_production_pdf.py`、`tests/test_production_printing.py`

- [x] 实现 `ProductionPrinting(store,documents)` 的status、submit、get、active_output_ids、start、stop，导出 `parse_pages(value,total_pages)`。
- [x] 以 `documents.hold` 校验并复制PDF，固定private配置队列，用用户/request_id去重，串行提交，重启不重投状态不明任务。
- [x] UNO子进程只读取受控manifest、PDF、打印机和页码；独立named pipe/profile，禁宏和外链，超时退出自有进程。终态删副本而保留任务记录。
- [x] mock runner测试，不向实体/云打印机提交。

### Task 3: HTTP及打印界面

**Files:** `app/server.py`、`app/production_print_views.py`、`web/production-documents.js`、`web/document-print.js`、`web/document-print.css`、`web/styles.css`、对应路由/前端测试。

- [x] 将文件验证到实际发送放在hold内；将ArtifactsExpired映射410；服务启动/关闭实际连接维护与队列线程。
- [x] 实现 `GET /api/production/printing`、`POST /api/production/print-jobs`、`GET /api/production/print-jobs/{id}`；提交要求网页session+CSRF/来源，有查看账号可打印，只读token不可提交。
- [x] 实现同源 `GET /api/production/document-files/{id}/print?pages=...` 打印页；实际图片全部载入后启用系统打印，提供错误/重试，afterprint不当出纸完成。
- [x] 预览旁添加打印入口、页码/份数与目标确认、请求防重与队列结果；失效资料提示重新生成。手机换行及键盘焦点沿用现有样式。

### Task 4: 版本与验收

- [x] 同步VERSION、app、两套页面、资源参数、CHANGELOG、README、自动化说明标题及三个V0.3.31入口，保留旧入口。
- [x] 隔离数据测试真实7日清理，检查原件和人工内容保留；实际打印页检查纸张分页与图片完整，不提交实物任务。
- [x] 运行完整Python与Node回归、Impeccable检测器，并检查电脑/手机截图。
- [x] 核验项目PID/工作区/整理状态后重启正式服务，核对健康、打印配置可用和业务表摘要；本机提交，询问是否发布GitHub。
