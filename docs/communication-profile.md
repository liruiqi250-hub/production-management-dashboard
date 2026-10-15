# 本机沟通理解资料

V0.3.6起，生产自动整理在每一轮分析前读取固定的本机文件 `private/communication_profiles_V1.0.0.json`。资料作为 DATA.communication_profile 传入无工具的分析器；不自动执行其中的聊天内容，也不改采集范围。它随本轮生产与售后分析输入一起使用，但只适用于所列生产群和人员，不据此认定售后维修完成。

没有文件时兼容原流程。存在文件但结构、来源、日期、办公室身份或大小无效时，该轮分析停止，原正式台账保留。最大128KiB；schema必须为1；最多20名办公室人员、40条规则和80个案例。实际覆盖必须包含在请求范围内，时间必须带时区。

固定顶层字段：schema、version、updated_at、requested_period、observed_period、coverage_note、speakers、rules、examples。两种period均为含start/end的对象。speakers逐项含name、aliases、identity_sha256、notes，昵称须和本机office_staff.json一致。examples逐项含speaker、source_id、refs、context、meaning、boundary，refs必须属于同一个production_01至production_14来源；不接受任意文件路径或外部URL。

每轮任务目录保存 `communication_profile_receipt.json`：是否实际加载、资料版本、SHA256、请求和实际覆盖范围、规则和案例数量。文件在每次整理时重新读取，上一轮快照导入不会清除这份本机资料。

每次验证再次载入最新版手册，并保存communication_validation_receipt.json与台账中的资料凭据。日常和手动任务必须让验证凭据与本轮分析凭据一致；若分析期间手册被更新或移除，停止写入并要求按新版重新复核，不能继续使用旧语境结论。

自然语言的“可以/好/嗯/做起走”等需结合具体提问、阶段、对象及后续反馈。现场已进入后道可说明前道完成或转序，但不能改写为客户认可或最终质量验收。未来计划、估计、收悉、仅有媒体占位和语音中的含糊关键字继续保留待核。

历史学习案例不能作为本轮业务证据，当前候选仍逐条回查当轮原消息。已有完成阶段与新增剩余工作分别记录，办公室跟进人不等于实际制作师傅。读取资料本身不启用自动关闭；V0.3.7在本机管理员另行持续授权后可自动完成有依据的阶段，最终验收仍分开核对。

持续授权保存在固定的 `private/context_review_policy.json`，字段为enabled布尔值、scope固定production_stage_only、authorized_by本机有效管理员账号、authorized_on明确授权日期。没有文件时关闭自动处理。每轮方案与结果分别归档为stage_review_plans.json、stage_review_result.json，明确产品、阶段、原消息、上下文和剩余事项。程序在台账导入的同一事务写入阶段完成及Codex自动复核审计，人工状态优先，不冒称员工手动验收。

资料、沟通理解手册、原件、媒体、数据库及本机复核结果只保留private，不进入Git源码或便携包。迁移需用户授权的单独本机复制和核验，不应为了“开箱即用”将公司聊天随通用运行依赖发布。
