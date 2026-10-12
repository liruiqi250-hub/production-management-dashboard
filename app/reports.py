"""V0.2.0 生产日报、周报及可打印/可编辑导出，仅使用标准库。

报表直接采用来源快照记载的事项状态，不根据聊天关键词推断维修完成。
消息覆盖始终描述来源快照；事项统计描述精选事项，两者不相加。
"""

from __future__ import annotations

import copy
import csv
import hashlib
import html
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app import __version__ as SOFTWARE_VERSION


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
OPEN_STATUSES = {"待确认", "待处理", "处理中", "待验证"}
SAMPLE_LIMITATION = "本报告基于首轮精选事项，非全量故障统计；审核状态逐项列示，事项数、问题数与来源消息量分别统计。"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _date(value: Any) -> date | None:
    """将明确的 ISO 日期/时间转为中国自然日；未知日期不推断。"""
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    value = _text(value).strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(SHANGHAI).date() if parsed.tzinfo else parsed.date()
    except (ValueError, OverflowError):
        return None


def _unique(items: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        key = _text(item.get("id")) or id(item)
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _priority(item: dict, end: date) -> tuple:
    deadline = _date(item.get("deadline"))
    return (0 if deadline and deadline < end else 1,
            0 if item.get("category") == "问题" else 1,
            0 if not _text(item.get("owner")).strip() else 1,
            deadline or date.max, _text(item.get("first_seen")), _text(item.get("id")))


def build_report(snapshot: dict, kind: str, end_date: str) -> dict:
    """生成日报或包含结束日期的七个自然日周报。

    sections 中 new/updated/closed 是独立事件口径，可能有交叉；
    updated 仅统计 first_seen 早于窗口且 last_seen 落在窗口的事项。
    unresolved 包括更早未结，以及明确在报告期末之后才解决的事项。
    缺少解决时间的已解决事项只列入日期缺口，不将 last_seen 作为解决日期。
    返回值与输入不共享可变对象，CSV 编码由下载服务使用 utf-8-sig。
    """
    if kind not in {"daily", "weekly"}:
        raise ValueError("报告类型仅支持 daily（日报）或 weekly（周报）")
    if not isinstance(end_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date):
        raise ValueError("结束日期必须为 YYYY-MM-DD")
    try:
        end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("结束日期无效") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("来源快照必须为字典")
    try:
        start = end - timedelta(days=6 if kind == "weekly" else 0)
    except OverflowError as exc:
        raise ValueError("结束日期过早，无法构成完整七天周报") from exc
    period = {"start": start.isoformat(), "end": end.isoformat()}
    source_period = copy.deepcopy(snapshot.get("period") or {})
    coverage = copy.deepcopy(snapshot.get("coverage") or {})
    source_start = _date(source_period.get("start"))
    source_end = _date(source_period.get("end") or source_period.get("cutoff"))
    coverage["source_period"] = source_period
    coverage["source_cutoff"] = _text(source_period.get("cutoff") or source_period.get("end"))
    coverage["matches_report_period"] = source_start == start and source_end == end
    coverage["range_label"] = "来源快照覆盖范围"
    coverage["range_note"] = (
        "来源快照覆盖日期与报告日期一致；消息量仍为来源快照的采集量，不代表精选事项数量。"
        if coverage["matches_report_period"] else
        "来源快照覆盖范围与报告范围不一致，下列消息量及媒体覆盖属于来源快照，不能作为本期完整消息量。"
    )
    limitations = [SAMPLE_LIMITATION]
    for container in (snapshot, coverage):
        for value in container.get("limitations") or []:
            _append_unique(limitations, _text(value))
    _append_unique(limitations, coverage["range_note"])
    _append_unique(limitations, "状态及审核程度采用来源快照记载，待复核项不代表李睿琪已确认；收到、发视频、安排寄件和建议均不会自动计为已解决。")

    sections = {key: [] for key in (
        "attention", "new", "updated", "closed", "progress", "unresolved",
        "assignment_gaps", "closure_date_gaps", "date_gaps",
    )}
    future_evidence_count = 0
    after_end_update = False
    for original in snapshot.get("items") or []:
        if not isinstance(original, dict):
            _append_unique(limitations, "存在格式不完整的事项，未纳入日期统计，请补充规范化资料。")
            continue
        item = copy.deepcopy(original)
        # 清单短摘要仅供当前页面，不随报表尤其历史JSON输出。
        item.pop("list_summary", None)
        first = _date(item.get("first_seen"))
        last = _date(item.get("last_seen"))
        closed = _date(item.get("closed_at"))
        item["report_note"] = ""
        if first is not None and first > end:
            continue
        evidence = []
        for entry in item.get("evidence") or []:
            if not isinstance(entry, dict):
                continue
            evidence_date = _date(entry.get("time"))
            if evidence_date is not None and evidence_date > end:
                future_evidence_count += 1
            else:
                evidence.append(entry)
        item["evidence"] = evidence
        if last is not None and last > end:
            # 当前摘要不能安全重建历史；保留可定位的期末以前证据。
            item["summary"] = "来源快照在期末之后有更新，当前摘要不在历史报告展开；请依据截至期末的证据核对。"
            item["next_step"] = "请核对截至期末的进展与待办。"
            item["report_note"] = "来源快照含期后更新；期末状态仅依据明确解决日期判断。"
            after_end_update = True
        if first is None:
            item["report_note"] = (item["report_note"] + " 首次发生日期缺失或无效，未计入本期事件和期末未结统计。").strip()
            sections["date_gaps"].append(item)
            _append_unique(limitations, "部分事项首次发生日期待核，未计入本期事件和期末未结统计。")
            continue
        if last is None:
            _append_unique(limitations, "部分事项最近出现日期缺失或无效，未计为本期更新。")
        if start <= first <= end:
            sections["new"].append(item)
        if first < start and last is not None and start <= last <= end:
            sections["updated"].append(item)
        status = item.get("status")
        if status == "已解决":
            if closed is None or closed < first:
                item["report_note"] = (item["report_note"] + " 解决日期缺失、无效或早于首次发生，未计为本期解决。").strip()
                sections["closure_date_gaps"].append(item)
            elif start <= closed <= end:
                sections["closed"].append(item)
            elif closed > end:
                item["report_note"] = (item["report_note"] + " 截至报告期末未结；当前已解决，解决日期为 " + _text(item.get("closed_at")) + "。").strip()
                sections["unresolved"].append(item)
        elif status in OPEN_STATUSES:
            sections["unresolved"].append(item)
        else:
            item["report_note"] = (item["report_note"] + " 状态不在统一状态表，未计入未结/解决统计。").strip()
            sections["date_gaps"].append(item)
            _append_unique(limitations, "部分事项状态待规范，未计入未结或解决统计。")

    if sections["closure_date_gaps"]:
        _append_unique(limitations, "已解决事项存在解决日期缺口，未计入本期解决；最近出现时间不作为解决时间。")
    if future_evidence_count:
        _append_unique(limitations, f"已从报告正文和台账排除 {future_evidence_count} 条明确晚于期末的证据。")
    if after_end_update:
        _append_unique(limitations, "历史报告无法从当前快照完整还原过去的负责人、期限和状态变更；含期后更新的摘要已收起，请结合期末以前证据复核。")
    sections["unresolved"].sort(key=lambda row: _priority(row, end))
    sections["attention"] = sections["unresolved"][:8]
    sections["progress"] = _unique(sections["new"] + sections["updated"] + sections["closed"])
    sections["assignment_gaps"] = [row for row in sections["unresolved"]
                                    if not _text(row.get("owner")).strip()
                                    or not _text(row.get("deadline")).strip()]
    visible = _unique(sections["unresolved"] + sections["progress"]
                      + sections["closure_date_gaps"] + sections["date_gaps"])
    counts = {
        "new": len(sections["new"]), "updated": len(sections["updated"]),
        "closed": len(sections["closed"]), "unresolved": len(sections["unresolved"]),
        "missing_owner": sum(not _text(row.get("owner")).strip() for row in sections["unresolved"]),
        "missing_deadline": sum(not _text(row.get("deadline")).strip() for row in sections["unresolved"]),
        "selected_items": len(visible),
        "closed_without_date": len(sections["closure_date_gaps"]),
        "problems": sum(row.get("category") == "问题" for row in visible),
        "overdue": sum(_date(row.get("deadline")) is not None and _date(row.get("deadline")) < end
                       for row in sections["unresolved"]),
    }
    label = "日报" if kind == "daily" else "周报"
    summary = [f"本期新增 {counts['new']} 项，更早事项本期更新 {counts['updated']} 项，有明确解决日期的本期解决 {counts['closed']} 项。",
               f"截至期末仍未结 {counts['unresolved']} 项，其中期限早于期末且未结 {counts['overdue']} 项。",
               f"未结事项中负责人空缺 {counts['missing_owner']} 项、期限空缺 {counts['missing_deadline']} 项，需补充明确安排。"]
    return {
        "title": f"生产管理{label} · {end.isoformat()}", "kind": kind,
        "version": SOFTWARE_VERSION,
        "source_schema_version": _text(snapshot.get("version")) or "0.2.0",
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "source_generated_at": _text(snapshot.get("generated_at")),
        "period": period, "counts": counts, "sections": sections,
        "items": visible, "coverage": coverage, "limitations": limitations,
        "executive_summary": summary,
        "count_note": "新增、更新、解决分别按事件计数，可能交叉，不相加为事项总数；更新仅含更早发生且本期最近出现的事项。",
    }


def _esc(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _paragraph(value: Any, fallback: str = "待补充") -> str:
    return _esc(value if _text(value).strip() else fallback).replace("\n", "<br>")


def _media_text(media: Any) -> str:
    if not isinstance(media, dict):
        return _text(media)
    names = {"images_requested": "图片记录", "images_exported": "已导出图片",
             "voices_requested": "语音记录", "voices_exported": "已导出语音",
             "voice_transcripts": "本地语音转写", "videos_requested": "视频记录",
             "videos_exported": "已导出视频", "files_requested": "附件记录",
             "files_exported": "已导出附件", "missing_video": "缺失视频",
             "missing_image": "缺失图片", "missing_voice": "缺失语音", "missing_file": "缺失附件"}
    parts = [names.get(key, key) + "：" + _text(value) for key, value in media.items()]
    for label, requested, exported in (("图片", "images_requested", "images_exported"),
                                       ("语音", "voices_requested", "voices_exported"),
                                       ("视频", "videos_requested", "videos_exported"),
                                       ("附件", "files_requested", "files_exported")):
        total, actual = media.get(requested), media.get(exported)
        if isinstance(total, int) and isinstance(actual, int) and total >= actual:
            parts.append(label + "导出缺口：" + str(total - actual))
    return "；".join(parts)


def _evidence_html(item: dict, rendered: set[str]) -> str:
    entries = item.get("evidence") or []
    if not entries:
        return '<div class="muted">证据定位待补充</div>'
    key = _text(item.get("id")) or _text([item.get("source_id"), item.get("title")])
    anchor = "evidence-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    locators = [_text(entry.get("locator")) or "定位待补充" for entry in entries]
    locator_text = "；".join(dict.fromkeys(locators[:3]))
    if len(entries) > 3:
        locator_text += f"；另 {len(entries) - 3} 条见完整证据"
    compact = '<div class="evidence-locators">证据定位：' + _esc(locator_text) + '</div>'
    if key in rendered:
        return compact + '<a class="evidence-link" href="#' + anchor + '">完整证据见前表折叠项</a>'
    rendered.add(key)
    result = []
    for entry in entries:
        result.append('<div class="evidence"><strong>' + _esc(entry.get("locator") or "定位待补充")
                      + '</strong><br>' + _esc(entry.get("time")) + " · " + _esc(entry.get("sender"))
                      + '<br>' + _paragraph(entry.get("text"), "原文待补充") + '</div>')
    return (compact + '<details class="evidence-details" id="' + anchor + '"><summary>展开完整证据（'
            + str(len(entries)) + '条）</summary>' + "".join(result) + '</details>')


def _item_table(items: list[dict], rendered: set[str], empty: str = "暂无符合此范围的事项。") -> str:
    if not items:
        return '<p class="muted">' + _esc(empty) + '</p>'
    rows = []
    for item in items:
        scope = ('<br><span class="muted">关闭范围：' + _esc(item["closure_scope"]) + '</span>') if item.get("closure_scope") else ""
        note = ('<p class="warning">' + _esc(item["report_note"]) + '</p>') if item.get("report_note") else ""
        rows.append('<tr><td><strong>' + _paragraph(item.get("title"), "未命名事项") + '</strong>'
                    + '<br><span class="muted">' + _esc(item.get("id")) + " · " + _esc(item.get("category"))
                    + '<br>' + _esc(item.get("group")) + " / " + _esc(item.get("source_id")) + '</span>'
                    + note + _evidence_html(item, rendered) + '</td><td>' + _paragraph(item.get("status"), "状态待核")
                    + '<br>负责人：' + _paragraph(item.get("owner"), "未指定")
                    + '<br><span class="muted">审核：' + _paragraph(item.get("review_state"), "待审核")
                    + '</span>' + scope + '</td><td>' + _paragraph(item.get("deadline"), "未指定")
                    + '<br><span class="muted">首次：' + _esc(item.get("first_seen"))
                    + '<br>快照最近出现：' + _esc(item.get("last_seen"))
                    + '<br>解决日期：' + _paragraph(item.get("closed_at"), "未记录") + '</span></td><td>'
                    + _paragraph(item.get("summary")) + '<br><strong>下一步：</strong>'
                    + _paragraph(item.get("next_step")) + '</td></tr>')
    return ('<table class="items"><colgroup><col style="width:40%"><col style="width:18%">'
            '<col style="width:19%"><col style="width:23%"></colgroup><thead><tr>'
            '<th>事项及证据定位</th><th>状态、负责人及确认范围</th><th>期限及日期依据</th><th>进展与下一步</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>')


def _attention_list(items: list[dict]) -> str:
    if not items:
        return '<p class="muted">暂无需优先列示的事项。</p>'
    return '<ul class="attention-list">' + "".join(
        '<li><strong>' + _paragraph(item.get("title"), "未命名事项") + '</strong>（'
        + _esc(item.get("id")) + ' · ' + _esc(item.get("status")) + '）<br>下一步：'
        + _paragraph(item.get("next_step")) + '</li>' for item in items) + '</ul>'


def _assignment_table(items: list[dict]) -> str:
    if not items:
        return '<p class="muted">暂无负责人或期限空缺。</p>'
    rows = []
    for item in items:
        missing = []
        if not _text(item.get("owner")).strip():
            missing.append("缺负责人")
        if not _text(item.get("deadline")).strip():
            missing.append("缺期限")
        rows.append('<tr><td>' + _paragraph(item.get("title"), "未命名事项")
                    + '<br><span class="muted">' + _esc(item.get("id")) + '</span></td><td>'
                    + _esc("、".join(missing)) + '</td></tr>')
    return ('<table class="assignment-gaps"><colgroup><col style="width:75%"><col style="width:25%">'
            '</colgroup><thead><tr><th>事项</th><th>待补充安排</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table>')


def render_report_html(report: dict) -> str:
    """返回中文完整 HTML；所有来源字段均作为转义后的文字输出。"""
    sections = report.get("sections") or {}
    coverage = report.get("coverage") or {}
    period = report.get("period") or {}
    source_period = coverage.get("source_period") or {}
    counts = report.get("counts") or {}
    blocks = []
    rendered_evidence: set[str] = set()
    for name, key, description in (
        ("主要关注", "attention", "按超期未结、问题及负责人空缺优先列示，最多8项；全部未结见下表。"),
        ("本期进展 / 确认", "progress", "包括本期新增、更早事项的本期更新和有明确日期的本期解决；关闭范围仅限来源证据明确确认的对应节点，李睿琪复核状态逐项列示。"),
        ("本期解决", "closed", "仅列已解决且明确解决日期落在本期的事项；不以收到、视频或最近出现时间代替完成证据。"),
        ("截至期末未结事项", "unresolved", "包括报告窗口以前发生的历史未结，不因滚出七天窗口而消失。"),
        ("负责人及期限空缺", "assignment_gaps", "以下未结事项需要补充负责人或期限。"),
        ("解决日期缺口", "closure_date_gaps", "当前状态为已解决，但无法可靠归属解决日期，未计入本期解决。"),
        ("日期 / 状态待核", "date_gaps", "日期或状态不能可靠归属的事项单独列示，不计入相应事件统计。"),
    ):
        items = sections.get(key) or []
        if key == "attention":
            content = _attention_list(items)
        elif key == "assignment_gaps":
            content = _assignment_table(items)
        else:
            content = _item_table(items, rendered_evidence)
        blocks.append('<section><h2>' + name + '</h2><p class="muted">' + description + '</p>'
                      + content + '</section>')
    group_rows = []
    for group in coverage.get("groups") or []:
        group_rows.append('<tr><td>' + _esc(group.get("group")) + '</td><td>' + _esc(group.get("source_id"))
                          + '</td><td>' + _esc(group.get("message_count", "未记录")) + '</td></tr>')
    group_table = ('<table><thead><tr><th>群 / 来源</th><th>来源编号</th><th>来源快照消息量</th></tr></thead><tbody>'
                   + "".join(group_rows) + '</tbody></table>') if group_rows else '<p class="muted">暂无群覆盖明细。</p>'
    cards = "".join('<div class="metric"><b>' + _esc(counts.get(key, 0)) + '</b><span>' + label + '</span></div>'
                    for key, label in (("new", "本期新增"), ("updated", "更早事项本期更新"),
                                       ("closed", "本期解决"), ("unresolved", "期末未结")))
    return ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">' + '<meta name="viewport" content="width=device-width,initial-scale=1">' + '<title>' + _esc(report.get("title")) + '</title>' + '''
<style>
@page { size: A4 portrait; margin: 13mm 11mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #18212d; background: #f1f4f8; font: 13px/1.6 "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }
main { max-width: 1040px; margin: 24px auto; padding: 32px; background: white; }
h1 { margin: 0 0 10px; font-size: 25px; } h2 { font-size: 18px; margin: 24px 0 8px; break-after: avoid; }
p { margin: 8px 0; } .meta, .muted { color: #526175; font-size: 11px; }
.warning { color: #70421b; background: #fff6e4; padding: 6px; }
.metrics { display: flex; gap: 10px; margin: 18px 0; } .metric { border: 1px solid #d5deea; padding: 10px; flex: 1; }
.metric b { display: block; font-size: 24px; } .metric span { font-size: 12px; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 8px 0 16px; }
th, td { border: 1px solid #ccd6e2; padding: 7px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #eaf0f7; font-weight: 600; } thead { display: table-header-group; }
tr { break-inside: avoid; } .items { font-size: 11px; } .evidence { border-top: 1px dashed #cbd5e1; margin-top: 7px; padding-top: 5px; color: #46576b; font-size: 10px; white-space: normal; }
.evidence-locators { margin-top: 6px; color: #46576b; font-size: 10px; } .evidence-details summary, .evidence-link { color: #355e85; font-size: 10px; cursor: pointer; }
li { margin: 5px 0; overflow-wrap: anywhere; } .toolbar { margin-bottom: 16px; }
button { padding: 8px 16px; cursor: pointer; } footer { margin-top: 24px; padding-top: 10px; border-top: 1px solid #ccd6e2; }
@media print { body { background: white; font-size: 10px; } main { margin: 0; padding: 0; max-width: none; } .toolbar, details.evidence-details, .evidence-link { display: none !important; } h1 { font-size: 20px; } h2 { font-size: 15px; } .items { font-size: 9px; } .evidence-locators, .meta, .muted { font-size: 8px; } th, td { padding: 5px; } }
@media screen and (max-width: 600px) { main { margin: 0; padding: 14px; } .metrics { flex-wrap: wrap; } .metric { flex-basis: 40%; } th, td { padding: 4px; } .items { font-size: 10px; } }
</style></head><body><main><div class="toolbar"><button onclick="window.print()">打印 / 另存为 PDF</button></div>'''
        + '<h1>' + _esc(report.get("title")) + '</h1><p class="meta">版本 V' + _esc(report.get("version"))
        + ' · 报告日期：' + _esc(period.get("start")) + ' 至 ' + _esc(period.get("end"))
        + '（中国自然日）<br>报告生成：' + _esc(report.get("generated_at"))
        + '<br>来源快照生成：' + _esc(report.get("source_generated_at"))
        + ' · 来源快照统计截止：' + _esc(coverage.get("source_cutoff") or "未记录") + '</p>'
        + '<p class="warning">' + _esc(SAMPLE_LIMITATION) + '</p><div class="metrics">' + cards + '</div>'
        + '<p class="muted">证据定位默认显示最多3条；电子稿可展开完整原文，重复事项仅在首次详情表保留原文。打印稿保留定位，完整证据可在电子稿或CSV台账复核。</p>'
        + '<p class="muted">' + _esc(report.get("count_note")) + '</p><ul>'
        + "".join('<li>' + _esc(value) + '</li>' for value in report.get("executive_summary") or []) + '</ul>'
        + "".join(blocks) + '<section><h2>数据覆盖、截止时间与缺口</h2><p><strong>来源快照覆盖范围：</strong>'
        + _esc(source_period.get("start") or "未记录") + ' 至 ' + _esc(source_period.get("end") or source_period.get("cutoff") or "未记录")
        + '<br><strong>来源快照统计截止：</strong>' + _esc(coverage.get("source_cutoff") or "未记录")
        + '<br><strong>来源快照消息量：</strong>' + _esc(coverage.get("message_count", "未记录"))
        + ' · 本报告列示精选事项：' + _esc(counts.get("selected_items", 0))
        + ' · 其中问题类事项：' + _esc(counts.get("problems", 0)) + '</p><p class="warning">'
        + _esc(coverage.get("range_note")) + '</p>' + group_table + '<p><strong>媒体覆盖与缺失：</strong>'
        + _paragraph(_media_text(coverage.get("media")), "未记录媒体覆盖，请补充核对。") + '</p><ul>'
        + "".join('<li>' + _esc(value) + '</li>' for value in report.get("limitations") or []) + '</ul></section>'
        + '<footer class="muted">本稿用于生产跟进与复核。证据定位沿用来源记录，关闭范围仅限对应确认节点。</footer>'
        + '</main></body></html>')


def _csv_cell(value: Any) -> str:
    value = _text(value)
    # Excel 会忽略部分前置空白；BOM 和控制字符也不能绕过公式保护。
    probe = value
    while probe and (probe[0].isspace() or probe[0] == "\ufeff"):
        probe = probe[1:]
    if (probe and probe[0] in "=+@-") or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def _brief_text(value: Any, limit: int, fallback: str = "待补充") -> str:
    """先按文字截短再转义，避免切断 HTML 实体。"""
    value = " ".join(_text(value).split()) or fallback
    return _esc(value if len(value) <= limit else value[:limit] + "…")


def render_report_brief_html(report: dict) -> str:
    """PDF专用管理简报：每项仅列一次，完整原文仍在详情HTML/CSV。"""
    period = report.get("period") or {}
    counts = report.get("counts") or {}
    coverage = report.get("coverage") or {}
    source_period = coverage.get("source_period") or {}
    sections = report.get("sections") or {}
    cards = "".join('<div class="metric"><b>' + _esc(counts.get(key, 0)) + '</b><span>' + label + '</span></div>'
                    for key, label in (("new", "本期新增"), ("updated", "更早事项本期更新"),
                                       ("closed", "本期解决"), ("unresolved", "期末未结")))
    attention = sections.get("attention") or []
    attention_html = '<ul class="attention">' + "".join(
        '<li><strong>' + _brief_text(item.get("title"), 48) + '</strong>：'
        + _brief_text(item.get("next_step"), 65) + '</li>' for item in attention[:5]) + '</ul>'
    if not attention:
        attention_html = '<p class="muted">暂无需优先列示的事项。</p>'
    rows = []
    for item in report.get("items") or []:
        evidence = item.get("evidence") or []
        review = _text(item.get("review_state"))
        manual = "手工" in review or any(_text(entry.get("locator")).startswith("manual:") for entry in evidence)
        prioritized = sorted(evidence, key=lambda entry: 0 if (
            _text(entry.get("locator")).startswith("manual:") and manual
            or entry.get("time") == item.get("closed_at") and item.get("closed_at")
        ) else 1)
        locators = list(dict.fromkeys(_text(entry.get("locator")) for entry in prioritized if entry.get("locator")))[:2]
        locator_html = '<br>'.join(_esc(value) for value in locators) or "定位待补充"
        status_note = '<br><span class="manual-note">含人工补录；不等同原消息或客户验收</span>' if manual else ""
        note = '<p class="item-note">' + _esc(item["report_note"]) + '</p>' if item.get("report_note") else ""
        rows.append('<tr data-item-id="' + _esc(item.get("id")) + '"><td><strong>'
                    + _brief_text(item.get("title"), 70) + '</strong><br><span class="muted">'
                    + _esc(item.get("id")) + ' · ' + _esc(item.get("category")) + '<br>'
                    + _esc(item.get("group") or "来源群待核") + '</span><div class="locators">'
                    + locator_html + '</div></td><td><strong>' + _esc(item.get("status") or "状态待核")
                    + '</strong><br>负责人：' + _esc(item.get("owner") or "未指定")
                    + '<br>期限：' + _esc(item.get("deadline") or "未指定")
                    + '<br><span class="muted">' + _esc(review or "审核待核") + '</span>' + status_note
                    + '</td><td><strong>进展：</strong>' + _brief_text(item.get("summary"), 120)
                    + '<br><strong>下一步：</strong>' + _brief_text(item.get("next_step"), 80)
                    + '<div class="scope"><strong>阶段关闭范围：</strong>'
                    + _esc(item.get("closure_scope") or "未记录关闭范围；不能据状态推定客户验收。")
                    + '</div>' + note + '</td></tr>')
    table = ('<table class="items"><colgroup><col style="width:31%"><col style="width:20%">'
             '<col style="width:49%"></colgroup><thead><tr><th>事项 / 来源 / 证据定位</th>'
             '<th>状态 / 负责人 / 期限</th><th>进展、下一步与阶段关闭范围</th></tr></thead><tbody>'
             + "".join(rows) + '</tbody></table>') if rows else '<p>本期无可列事项。</p>'
    limitations = "；".join(_text(value) for value in report.get("limitations") or [])
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>'
            + _esc(report.get("title")) + ' · 管理简报</title><style>' + '''
@page { size: A4 portrait; margin: 12mm 11mm; }
* { box-sizing: border-box; }
body { color: #18212d; font: 11px/1.42 "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; margin: 0; }
main { max-width: 1000px; margin: auto; } h1 { font-size: 22px; margin: 0 0 7px; }
h2 { font-size: 14px; margin: 15px 0 5px; break-after: avoid; } p { margin: 5px 0; }
.muted, .meta { color: #526175; font-size: 9px; } .notice { background: #fff5df; padding: 6px 8px; font-size: 10px; }
.metrics { display: flex; gap: 8px; margin: 10px 0; } .metric { border: 1px solid #d5deea; padding: 7px 9px; flex: 1; }
.metric b { display: block; font-size: 21px; } .metric span { font-size: 10px; }
.attention { padding-left: 18px; margin: 5px 0; font-size: 10px; } .attention li { margin: 4px 0; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; margin: 7px 0; font-size: 10px; }
th, td { border: 1px solid #ccd6e2; padding: 5px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #eaf0f7; } thead { display: table-header-group; } tr { break-inside: avoid; }
.locators { font-size: 8px; color: #526175; margin-top: 4px; } .scope { margin-top: 4px; padding-top: 3px; border-top: 1px dashed #d4dce5; }
.manual-note, .item-note { color: #70421b; font-size: 9px; } .coverage { font-size: 9px; line-height: 1.45; }
footer { border-top: 1px solid #ccd6e2; margin-top: 9px; padding-top: 5px; font-size: 9px; color: #526175; }
@media screen { body { background: #f1f4f8; } main { background: white; padding: 24px; } }
</style></head><body><main>'''
            + '<h1>' + _esc(report.get("title")) + ' · 管理简报</h1><p class="meta">版本 V'
            + _esc(report.get("version")) + ' · 报告日期：' + _esc(period.get("start")) + ' 至 '
            + _esc(period.get("end")) + '（中国自然日）<br>来源快照生成：'
            + _esc(report.get("source_generated_at")) + ' · 快照统计截止：'
            + _esc(coverage.get("source_cutoff") or "未记录") + '</p><p class="notice">'
            + _esc(SAMPLE_LIMITATION) + ' 本稿为简报节选，长文字以“…”标示；全部列示事项保留，完整证据见HTML详情、CSV台账与生产看板。</p>'
            + '<div class="metrics">' + cards + '</div><p>'
            + ' '.join(_esc(value) for value in report.get("executive_summary") or []) + '</p><p class="muted">'
            + _esc(report.get("count_note")) + '</p><h2>主要关注（最多5项）</h2>' + attention_html
            + '<h2>事项简表（每项一次，含全部历史未结）</h2><p class="muted">状态为当前快照记载；员工手工补录完成单独标识，不代表李睿琪已验收或客户已认可。已解决仅关闭下列阶段范围，不推定整机完工或发货。</p>'
            + table + '<h2>来源覆盖、媒体缺口与统计限制</h2><div class="coverage"><p><strong>来源快照覆盖范围：</strong>'
            + _esc(source_period.get("start") or "未记录") + ' 至 '
            + _esc(source_period.get("end") or source_period.get("cutoff") or "未记录")
            + '；来源群 ' + _esc(len(coverage.get("groups") or [])) + ' 个；来源快照消息 '
            + _esc(coverage.get("message_count", "未记录")) + ' 条；本稿列示事项 '
            + _esc(counts.get("selected_items", len(report.get("items") or []))) + ' 项。</p><p><strong>范围提示：</strong>'
            + _esc(coverage.get("range_note")) + '</p><p><strong>媒体覆盖与缺失：</strong>'
            + _esc(_media_text(coverage.get("media")) or "未记录，请复核。") + '</p><p>'
            + _esc(limitations) + '</p></div><footer>简报只压缩文字，不删未结事项；原消息证据与人工补录证据分别标示，完整内容见HTML详情、CSV台账与看板。</footer></main></body></html>')


def render_report_csv(report: dict) -> str:
    """返回可编辑中文台账字符串，下载服务应以 utf-8-sig 编码。"""
    headers = ["报告类型", "报告开始日期", "报告结束日期", "事项编号", "事项", "类别", "状态",
               "负责人", "期限", "首次发生", "快照最近出现", "明确解决日期", "进展摘要", "下一步",
               "来源编号", "来源群", "审核状态", "关闭范围", "本期新增", "本期更新", "本期解决", "期末未结",
               "负责人期限空缺", "报告说明", "证据定位及原文", "来源快照覆盖范围", "来源快照统计截止",
               "来源快照消息量", "媒体覆盖与缺失", "统计口径及限制", "软件版本"]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)
    sections = report.get("sections") or {}
    memberships = {key: {_text(row.get("id")) for row in sections.get(key) or []}
                   for key in ("new", "updated", "closed", "unresolved", "assignment_gaps")}
    coverage = report.get("coverage") or {}
    source_period = coverage.get("source_period") or {}
    period = report.get("period") or {}
    source_range = _text(source_period.get("start") or "未记录") + " 至 " + _text(source_period.get("end") or source_period.get("cutoff") or "未记录")
    restrictions = "\n".join([_text(report.get("count_note"))] + [_text(x) for x in report.get("limitations") or []])
    items = report.get("items") or [{"title": "本期无可列事项", "report_note": "仍请核对来源快照覆盖与缺口。"}]
    for item in items:
        item_id = _text(item.get("id"))
        evidence = "\n\n".join(" | ".join(_text(entry.get(key)) for key in ("locator", "time", "sender", "text"))
                               for entry in item.get("evidence") or [])
        values = ["日报" if report.get("kind") == "daily" else "周报", period.get("start"), period.get("end"),
                  item.get("id"), item.get("title"), item.get("category"), item.get("status"),
                  item.get("owner"), item.get("deadline"), item.get("first_seen"), item.get("last_seen"),
                  item.get("closed_at"), item.get("summary"), item.get("next_step"), item.get("source_id"),
                  item.get("group"), item.get("review_state"), item.get("closure_scope")]
        values.extend("是" if item_id and item_id in memberships[key] else "" for key in memberships)
        values.extend([item.get("report_note"), evidence, source_range, coverage.get("source_cutoff"),
                       coverage.get("message_count"), _media_text(coverage.get("media")), restrictions, report.get("version")])
        writer.writerow([_csv_cell(value) for value in values])
    return output.getvalue()
