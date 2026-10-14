"""报表范围和导出安全测试；所有案例均为虚构测试数据。"""

import copy
import csv
import io
import unittest

from app.reports import build_report, render_report_brief_html, render_report_csv, render_report_html


def test_item(item_id, first, last=None, status="处理中", **extra):
    value = {
        "id": item_id,
        "title": "测试事项 " + item_id,
        "category": "问题",
        "status": status,
        "owner": "测试负责人",
        "deadline": "2026-10-13",
        "first_seen": first,
        "last_seen": last or first,
        "summary": "明确标记的测试进展",
        "next_step": "继续复核",
        "source_id": "test-source",
        "group": "测试群",
        "evidence": [{"locator": "test:" + item_id, "time": last or first,
                      "sender": "测试发送人", "text": "测试证据"}],
        "closure_scope": "",
        "review_state": "已审核",
    }
    value.update(extra)
    return value


def test_snapshot(items):
    return {
        "version": "0.2.0",
        "generated_at": "2026-10-12T16:30:00+08:00",
        "period": {"start": "2026-10-06", "end": "2026-10-12",
                   "cutoff": "2026-10-12T16:20:00+08:00"},
        "coverage": {"groups": [{"source_id": "test-source", "group": "测试群",
                                   "message_count": 9}],
                     "message_count": 9, "media": {"missing_video": 1},
                     "limitations": ["测试快照，人工精选"]},
        "items": items,
    }


class ReportRangeTests(unittest.TestCase):
    def test_daily_includes_only_same_day_events_and_past_unresolved(self):
        snapshot = test_snapshot([
            test_item("old-open", "2026-09-01", "2026-09-02"),
            test_item("updated", "2026-10-01", "2026-10-12"),
            test_item("new", "2026-10-12"),
            test_item("prior-closed", "2026-10-01", "2026-10-11", "已解决",
                      closed_at="2026-10-11"),
            test_item("closed", "2026-10-02", "2026-10-12", "已解决",
                      closed_at="2026-10-12"),
            test_item("future", "2026-10-13"),
        ])
        report = build_report(snapshot, "daily", "2026-10-12")
        self.assertEqual(report["period"], {"start": "2026-10-12", "end": "2026-10-12"})
        self.assertEqual({x["id"] for x in report["sections"]["new"]}, {"new"})
        self.assertEqual({x["id"] for x in report["sections"]["updated"]}, {"updated", "closed"})
        self.assertEqual({x["id"] for x in report["sections"]["closed"]}, {"closed"})
        self.assertEqual({x["id"] for x in report["sections"]["unresolved"]},
                         {"old-open", "updated", "new"})
        self.assertNotIn("future", {x["id"] for x in report["items"]})
        self.assertNotIn("prior-closed", {x["id"] for x in report["items"]})

    def test_weekly_includes_both_date_boundaries(self):
        report = build_report(test_snapshot([
            test_item("before", "2026-10-05", status="已解决", closed_at="2026-10-05"),
            test_item("first", "2026-10-06"),
            test_item("last", "2026-10-12"),
            test_item("after", "2026-10-13"),
        ]), "weekly", "2026-10-12")
        self.assertEqual(report["period"]["start"], "2026-10-06")
        self.assertEqual({x["id"] for x in report["sections"]["new"]}, {"first", "last"})
        self.assertEqual(report["counts"]["new"], 2)

    def test_resolved_without_closed_at_is_not_dated_from_last_seen(self):
        report = build_report(test_snapshot([
            test_item("unknown-close", "2026-10-01", "2026-10-12", "已解决")
        ]), "daily", "2026-10-12")
        self.assertEqual(report["counts"]["closed"], 0)
        self.assertEqual(report["counts"]["closed_without_date"], 1)
        self.assertEqual(report["sections"]["closure_date_gaps"][0]["id"], "unknown-close")
        self.assertIn("解决日期", "\n".join(report["limitations"]))

    def test_future_closed_at_keeps_item_unresolved_at_historical_end(self):
        report = build_report(test_snapshot([
            test_item("later-close", "2026-10-01", "2026-10-14", "已解决",
                      closed_at="2026-10-14")
        ]), "daily", "2026-10-12")
        self.assertEqual(report["counts"]["closed"], 0)
        self.assertEqual(report["counts"]["unresolved"], 1)
        self.assertIn("当前已解决", report["sections"]["unresolved"][0]["report_note"])

    def test_evidence_after_report_end_is_omitted_from_export(self):
        row = test_item("earlier-open", "2026-10-01", "2026-10-14", evidence=[
            {"locator": "test:old", "time": "2026-10-11", "sender": "甲", "text": "期末之前"},
            {"locator": "test:future", "time": "2026-10-14", "sender": "甲", "text": "未来测试证据"},
        ])
        report = build_report(test_snapshot([row]), "daily", "2026-10-12")
        self.assertEqual(len(report["items"][0]["evidence"]), 1)
        self.assertNotIn("未来测试证据", render_report_html(report))
        self.assertNotIn("未来测试证据", render_report_csv(report))

    def test_datetime_uses_shanghai_calendar_day(self):
        row = test_item("utc", "2026-10-11T17:00:00Z")
        report = build_report(test_snapshot([row]), "daily", "2026-10-12")
        self.assertEqual(report["counts"]["new"], 1)

    def test_missing_first_date_does_not_leak_known_future_progress(self):
        marker = "AFTER_PERIOD_SUMMARY_2026_10_14"
        report = build_report(test_snapshot([
            test_item("unknown-date", "bad-date", "2026-10-14", summary=marker, next_step=marker,
                      evidence=[{"locator": "test:future", "time": "2026-10-14", "text": marker}])
        ]), "daily", "2026-10-12")
        self.assertNotIn(marker, render_report_html(report))
        self.assertNotIn(marker, render_report_csv(report))
        self.assertIn("期后更新", "\n".join(report["limitations"]))

    def test_receipt_or_video_text_does_not_change_status(self):
        report = build_report(test_snapshot([
            test_item("receipt", "2026-10-12", status="待验证", summary="客户说收到，已发视频")
        ]), "daily", "2026-10-12")
        self.assertEqual(report["counts"]["closed"], 0)
        self.assertEqual(report["sections"]["unresolved"][0]["status"], "待验证")

    def test_stage_and_work_events_keep_open_followups_and_do_not_leak_future(self):
        row = test_item("stage", "2026-10-12", "2026-10-14", status="待验证",
                        evidence=[{"locator": "test:approval", "time": "2026-10-12", "sender": "业务", "text": "这个造型可以"},
                                  {"locator": "test:work", "time": "2026-10-14", "sender": "工厂", "text": "已经出货"}],
                        stage_confirmation={"time": "2026-10-12", "locator": "test:approval", "scope": "仅造型"},
                        work_completion={"time": "2026-10-14", "locator": "test:work", "scope": "仅出货反馈"})
        early = build_report(test_snapshot([row]), "daily", "2026-10-12")
        self.assertEqual(early["counts"]["stage_confirmed"], 1)
        self.assertEqual(early["counts"]["work_reported"], 0)
        self.assertEqual(early["counts"]["unresolved"], 1)
        later = build_report(test_snapshot([row]), "daily", "2026-10-14")
        self.assertEqual(later["counts"]["work_reported"], 1)
        self.assertEqual(later["counts"]["closed"], 0)

    def test_coverage_is_labelled_as_snapshot_when_daily_range_differs(self):
        report = build_report(test_snapshot([]), "daily", "2026-10-12")
        html = render_report_html(report)
        self.assertFalse(report["coverage"]["matches_report_period"])
        self.assertIn("来源快照覆盖范围", html)
        self.assertIn("2026-10-06", html)
        self.assertIn("2026-10-12T16:20:00+08:00", html)
        self.assertIn("非全量故障统计", html)
        self.assertNotIn("当日完整消息", html)

    def test_missing_owner_deadline_and_empty_snapshot(self):
        report = build_report(test_snapshot([
            test_item("missing", "2026-10-12", owner="", deadline=None)
        ]), "daily", "2026-10-12")
        self.assertEqual(report["counts"]["missing_owner"], 1)
        self.assertEqual(report["counts"]["missing_deadline"], 1)
        empty = build_report(test_snapshot([]), "daily", "2026-10-12")
        self.assertEqual(empty["counts"]["selected_items"], 0)
        self.assertIn("本期无可列事项", render_report_csv(empty))
        self.assertIn("暂无", render_report_html(empty))

    def test_invalid_inputs_raise_and_missing_dates_are_explicit(self):
        for kind, end in [("monthly", "2026-10-12"), ("daily", "2026-02-30"),
                          ("daily", "2026-10-12T00:00:00")]:
            with self.subTest(kind=kind, end=end), self.assertRaises(ValueError):
                build_report(test_snapshot([]), kind, end)
        report = build_report(test_snapshot([
            test_item("bad-date", "not-a-date")
        ]), "daily", "2026-10-12")
        self.assertEqual(report["counts"]["new"], 0)
        self.assertEqual(report["counts"]["unresolved"], 0)
        self.assertEqual(report["sections"]["date_gaps"][0]["id"], "bad-date")

    def test_build_does_not_mutate_source(self):
        snapshot = test_snapshot([test_item("source", "2026-10-12")])
        before = copy.deepcopy(snapshot)
        build_report(snapshot, "daily", "2026-10-12")
        self.assertEqual(snapshot, before)


class ReportExportTests(unittest.TestCase):
    def test_leader_brief_is_bounded_and_completion_export_is_daily(self):
        scope = '仅本轮造型确认，<script>不可扩展</script>，不含整机验收。'
        items = [test_item('brief-' + str(n), '2026-10-12', summary='长进展' * 100,
                           next_step='下一步' * 100,
                           stage_confirmation={'time': '2026-10-12', 'locator': 'test:brief-' + str(n), 'scope': scope})
                 for n in range(7)]
        report = build_report(test_snapshot(items), 'daily', '2026-10-12')
        output = render_report_brief_html(report)
        self.assertEqual(report['counts']['stage_confirmed'], 7)
        self.assertEqual(output.count('data-item-id='), 6)
        self.assertEqual(output.count('<li>'), 5)
        self.assertIn('另有 1 项', output)
        self.assertNotIn('<script>', output)
        self.assertIn('&lt;script&gt;不可扩展&lt;/script&gt;', output)
        self.assertIn('来源快照覆盖范围', output)
        completed = render_report_brief_html(report, completed_only=True)
        self.assertEqual(completed.count('data-item-id='), 7)
        self.assertNotIn('需要关注（最多5项）', completed)
        csv_completed = list(csv.DictReader(io.StringIO(render_report_csv(report, completed_only=True))))
        self.assertEqual(len(csv_completed), 7)
        self.assertEqual({row['类型'] for row in csv_completed}, {'业务认可阶段'})

    def test_evidence_is_collapsible_escaped_and_not_repeated(self):
        attack = '<script>evidence_only_payload</script>'
        evidence = [{"locator": "test:locator-" + str(n), "time": "2026-10-12",
                     "sender": "测试发送人", "text": attack + str(n)} for n in range(5)]
        report = build_report(test_snapshot([
            test_item("folded", "2026-10-12", owner="", deadline="", evidence=evidence)
        ]), "daily", "2026-10-12")
        output = render_report_html(report)
        self.assertIn('<details class="evidence-details"', output)
        self.assertNotIn('<details class="evidence-details" open', output)
        self.assertIn('details.evidence-details, .evidence-link { display: none !important; }', output)
        self.assertNotIn("<script>", output)
        self.assertEqual(output.count("&lt;script&gt;evidence_only_payload&lt;/script&gt;0"), 1)
        self.assertIn("展开完整证据（5条）", output)
        self.assertIn('class="attention-list"', output)
        self.assertIn('class="assignment-gaps"', output)
        self.assertIn("另 2 条见完整证据", output)
        self.assertIn("test:locator-4", output)
        csv_output = render_report_csv(report)
        self.assertIn(attack + "4", csv_output)

    def test_all_user_content_is_html_escaped(self):
        attack = '<script>alert("x")</script>&'
        snapshot = test_snapshot([test_item("x", "2026-10-12", title=attack,
                                  owner=attack, summary=attack, next_step=attack,
                                  group=attack, closure_scope=attack,
                                  evidence=[{"locator": attack, "time": "2026-10-12",
                                             "sender": attack, "text": attack}])])
        snapshot["coverage"]["groups"][0]["group"] = attack
        snapshot["coverage"]["limitations"] = [attack]
        result = render_report_html(build_report(snapshot, "daily", "2026-10-12"))
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertIn("@page", result)
        self.assertIn("A4", result)
        self.assertIn("table-header-group", result)

    def test_csv_formula_prefixes_and_unicode_round_trip(self):
        rows = [test_item(str(n), "2026-10-12", title=value)
                for n, value in enumerate(["=1+1", "+SUM(A1)", "@x", "-cmd",
                                           "  =1+1", "\t=1+1", "\ufeff=1+1", "正常中文，带逗号"])]
        output = render_report_csv(build_report(test_snapshot(rows), "daily", "2026-10-12"))
        parsed = list(csv.DictReader(io.StringIO(output)))
        by_id = {x["事项编号"]: x for x in parsed}
        for number in range(7):
            self.assertTrue(by_id[str(number)]["事项"].startswith("'"))
        self.assertEqual(by_id["7"]["事项"], "正常中文，带逗号")
        self.assertEqual(output.encode("utf-8-sig")[:3], b"\xef\xbb\xbf")
        self.assertIn("test:7", by_id["7"]["证据定位及原文"])


if __name__ == "__main__":
    unittest.main()
