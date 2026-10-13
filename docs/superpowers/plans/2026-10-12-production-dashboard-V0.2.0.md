# 生产管理看板 V0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已批准草图做成本机可登录、可管理事项、可导出日报周报且每日自动更新的V0.2.0。

**Architecture:** 微信采集与日期范围在本机完成；规范化snapshot将原始证据和业务事项分离，业务服务保留人工修改与未结历史。优先验证NocoBase运行底座，并保持可独立运行的本机网页与报表接口，厂外部署不在本轮范围。

**Tech Stack:** Windows PowerShell、Python 3.12标准库/SQLite、HTML/CSS/JavaScript、本机SenseVoice、候选NocoBase、Codex heartbeat自动任务。

---

### Task 1: 环境与版本基线

Files: `docs/superpowers/specs/2026-10-12-production-dashboard-V0.2.0.md`、`VERSION`、`.gitignore`、`CHANGELOG.md`。

- [ ] 核对官方NocoBase安装要求并实际检查本机Node、数据库与容器环境，采用能验证的本机运行方式。
- [ ] 将软件版本写为 `0.2.0`，忽略private、acquisition、analysis、运行日志、数据库、依赖目录；不把聊天提交Git。
- [ ] 在 `codex/production-dashboard-v0.2.0` 分支记录设计与源代码。

### Task 2: snapshot规范化与幂等导入

Files: `app/normalize.py`、`app/__init__.py`、`private/bootstrap_snapshot_V0.2.0.json`、`tests/test_normalize.py`。

- [ ] 用既有manifest及三个审核JSON构造真实初始化数据，绝不直接把801消息计成801问题。
- [ ] 实现接口：

```python
def build_bootstrap_snapshot(workspace): ...
def validate_snapshot(snapshot): ...
```

- [ ] 验证14群覆盖、18个精选事项候选、locator稳定去重、日期与媒体存在性；每项注明人工精选、非完整问题统计。

### Task 3: 日报周报引擎

Files: `app/reports.py`、`tests/test_reports.py`。

- [ ] 实现 `build_report(snapshot, kind, end_date)`，kind限定daily/weekly；返回title、period、counts、sections、coverage及limitations。
- [ ] 实现 `render_report_html(report)` 和 `render_report_csv(report)`；用户内容HTML转义，CSV公式前缀保护，utf-8-sig下载。
- [ ] 对日报当天、周报7天含首尾、以前未结保留、窗口外关闭不计当期解决、空数据/缺失媒体及Unicode做实际测试。

### Task 4: 可登录的本机服务与网页

Files: `app/server.py`、`app/store.py`、`web/index.html`、`web/app.js`、`web/styles.css`、`scripts/start_dashboard_V0.2.0.ps1`、`scripts/import_snapshot_V0.2.0.py`。

- [ ] 服务绑定127.0.0.1；健康检查只返回版本/就绪状态；其它业务API均验证服务端会话。
- [ ] 建立随机初始密码的副厂与厂长两个账号，scrypt哈希保存；密码仅放本机private文件。会话HttpOnly、SameSite=Strict；修改需CSRF token、Origin校验与角色检查。
- [ ] 持久化snapshot及人工owner/deadline/status；自动导入按事项id/evidence.locator幂等合并，人工修改保留。
- [ ] 实现 `GET /api/dashboard`、`PATCH /api/items/<id>`、`GET /api/reports?kind=daily|weekly&end=YYYY-MM-DD&format=html|csv`；导出必须登录。
- [ ] 网页按已批准草图实现三页、筛选、事项详情、可写工作台、日报周报两个按钮和CSV备用导出。

### Task 5: 自动更新与交付

Files: `scripts/daily_collect_V0.2.0.ps1`、`automation/每日生产整理_V0.2.0.md`、`README.md`、`outputs/V0.2.0/`。

- [ ] 动态计算中国时间当日/最近7天，调用已验证collector并写唯一运行目录；失败不替换last-good。
- [ ] 当前会话创建每天15:30 heartbeat，要求16:30前完成；采集后读取新消息复核更新事项，调用snapshot导入并生成日报周报；不发外部消息。
- [ ] 执行实际登录权限、修改持久化、导出文件中文、浏览器交互、窄屏布局检查；保存验证摘要和运行说明。
- [ ] 打开本机正式版交付，不声称厂外已上线。报告NocoBase实际状态、自动任务实际设置与尚未人工校对的媒体范围。
