"""清单摘要与原始事实、人工闭环和历史报表的隔离回归；只用虚构临时库。"""

import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from app.reports import build_report
from app.store import Store
from test_store_server import synthetic_snapshot


class ListSummaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dino-brief-tests-")
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.source = synthetic_snapshot()
        self.store.import_snapshot(self.source)
        self.item_id = self.source["items"][0]["id"]

    def item(self):
        return next(item for item in self.store.snapshot()["items"] if item["id"] == self.item_id)

    def test_saved_summary_persists_without_replacing_source_or_evidence(self):
        original_source = copy.deepcopy(self.source)
        original_item = self.item()
        with self.store.connect() as connection:
            before = dict(connection.execute("SELECT * FROM items WHERE id=?", (self.item_id,)).fetchone())

        self.store.set_list_summaries({self.item_id: "等待虚构测试人员确认造型"})

        item = self.item()
        self.assertEqual(item["list_summary"], "等待虚构测试人员确认造型")
        self.assertEqual(item["summary"], original_item["summary"])
        self.assertEqual(item["next_step"], original_item["next_step"])
        self.assertEqual(item["evidence"], original_item["evidence"])
        self.assertEqual(self.source, original_source)
        with self.store.connect() as connection:
            after = dict(connection.execute("SELECT * FROM items WHERE id=?", (self.item_id,)).fetchone())
        self.assertEqual(after, before)
        reopened = Store(self.directory.name).snapshot()["items"][0]
        self.assertEqual(reopened["list_summary"], item["list_summary"])

    def test_manual_completion_replaces_stale_pending_summary_with_actual_scope(self):
        self.store.set_list_summaries({self.item_id: "仍等待虚构客户确认全部成品"})
        scope = "仅虚构造型确认节点，成品制作尚未完成"
        with patch("app.store.now", return_value="2026-10-12T16:00:00+08:00"):
            item = self.store.update_item(self.item_id, {
                "status": "已解决", "closure_note": "虚构人员已确认本轮造型", "closure_scope": scope,
            }, "liruiqi")

        self.assertEqual(item["list_summary"], "已确认范围：" + scope)
        self.assertNotIn("仍等待", item["list_summary"])
        self.assertEqual(item["summary"], self.source["items"][0]["summary"])
        self.assertEqual(item["evidence"][:-1], self.source["items"][0]["evidence"])
        self.assertEqual(item["evidence"][-1]["text"], "虚构人员已确认本轮造型")
        self.store.import_snapshot(self.source)
        self.assertEqual(self.item()["list_summary"], "已确认范围：" + scope)

    def test_new_collection_summary_or_last_seen_invalidates_saved_text(self):
        for field, value in (("summary", "虚构新采集进展，原摘要已过期"),
                             ("last_seen", "2026-10-12T10:00:00+08:00")):
            with self.subTest(field=field):
                self.store.import_snapshot(self.source)
                self.store.set_list_summaries({self.item_id: "虚构旧的一句摘要"})
                incoming = copy.deepcopy(self.source)
                incoming["items"][0][field] = value
                if field == "last_seen":
                    incoming["items"][0]["evidence"].append({
                        "locator": "production_01|message\\message_1.db:2", "time": value,
                        "sender": "虚构测试人员", "text": "虚构新采集消息",
                    })
                    incoming["coverage"]["message_count"] = 2
                    incoming["coverage"]["groups"][0]["message_count"] = 2
                self.store.import_snapshot(incoming)
                item = self.item()
                self.assertEqual(item[field], value)
                self.assertEqual(item["list_summary"], self.source["items"][0]["next_step"])
                self.assertNotEqual(item["list_summary"], "虚构旧的一句摘要")
        with self.subTest(manual_update_masks_last_seen=True):
            with patch("app.store.now", return_value="2026-10-12T16:00:00+08:00"):
                self.store.update_item(self.item_id, {"owner": "张英"}, "liruiqi")
            self.store.set_list_summaries({self.item_id: "人工更新后保存的旧摘要"})
            incoming = copy.deepcopy(self.source)
            incoming["items"][0]["last_seen"] = "2026-10-12T12:00:00+08:00"
            incoming["items"][0]["evidence"].append({
                "locator": "production_01|message\\message_1.db:2", "time": "2026-10-12T12:00:00+08:00",
                "sender": "虚构测试人员", "text": "早于人工更新时间的新采集消息",
            })
            incoming["coverage"]["message_count"] = 2
            incoming["coverage"]["groups"][0]["message_count"] = 2
            self.store.import_snapshot(incoming)
            item = self.item()
            self.assertEqual(item["last_seen"], "2026-10-12T16:00:00+08:00")
            self.assertEqual(item["source_last_seen"], "2026-10-12T12:00:00+08:00")
            self.assertEqual(item["list_summary"], self.source["items"][0]["next_step"])

    def test_reports_exclude_current_list_summary_including_historical_json(self):
        incoming = copy.deepcopy(self.source)
        incoming["items"][0]["last_seen"] = "2026-10-12T10:00:00+08:00"
        incoming["items"][0]["evidence"].append({
            "locator": "production_01|message\\message_1.db:2", "time": "2026-10-12T10:00:00+08:00",
            "sender": "虚构测试人员", "text": "虚构期后采集消息",
        })
        incoming["coverage"]["message_count"] = 2
        incoming["coverage"]["groups"][0]["message_count"] = 2
        self.store.import_snapshot(incoming)
        saved_text = "仅当前清单可见的虚构短摘要"
        self.store.set_list_summaries({self.item_id: saved_text})
        snapshot = self.store.snapshot()
        for kind, end in (("daily", "2026-10-12"), ("weekly", "2026-10-12"),
                          ("daily", "2026-10-08")):
            with self.subTest(kind=kind, end=end):
                report = build_report(snapshot, kind, end)
                self.assertTrue(report["items"])
                output = json.dumps(report, ensure_ascii=False)
                self.assertNotIn('"list_summary"', output)
                self.assertNotIn(saved_text, output)
                if end == "2026-10-08":
                    self.assertIn("当前摘要不在历史报告展开", report["items"][0]["summary"])
        self.assertEqual(snapshot["items"][0]["list_summary"], saved_text)
        self.assertEqual(self.item()["list_summary"], saved_text)

    def test_invalid_summary_batch_is_rejected_without_partial_save(self):
        self.store.set_list_summaries({self.item_id: "虚构已保存摘要"})
        for summaries in ([], {self.item_id: " "}, {self.item_id: "字" * 81},
                          {self.item_id: None}, {self.item_id: "不应部分写入", "MISSING": "不存在事项"}):
            with self.subTest(summaries=summaries):
                with self.assertRaises(ValueError):
                    self.store.set_list_summaries(summaries)
                self.assertEqual(self.item()["list_summary"], "虚构已保存摘要")


if __name__ == "__main__":
    unittest.main()
