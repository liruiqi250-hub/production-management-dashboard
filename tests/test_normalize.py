"""真实初始化材料与规范化合同的必要回归检查。"""

import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from app.normalize import REVIEW_PENDING, _read_json, build_bootstrap_snapshot, validate_snapshot


WORKSPACE = Path(__file__).resolve().parents[1]


class NormalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required_local = ["acquisition/V0.1.2/pilot_001/collection_manifest_V0.1.2.json",
                          "acquisition/V0.1.3/batch_001/collection_manifest_V0.1.3.json",
                          "analysis/sample_review_V0.1.0/样本事项核查.json",
                          "analysis/file_review_V0.1.0/file_review_V0.1.0.json",
                          "analysis/remaining_sample_review_V0.1.0/remaining_sample_review_V0.1.0.json",
                          "analysis/coverage_V0.1.0.json", "private/stage_closures_V0.2.0.json"]
        if any(not (WORKSPACE / path).is_file() for path in required_local):
            raise unittest.SkipTest("真实采集材料仅在授权本机保存；仓库副本跳过本机证据回归")
        cls.snapshot = build_bootstrap_snapshot(WORKSPACE)
        cls.by_id = {item["id"]: item for item in cls.snapshot["items"]}

    def mutated(self):
        return copy.deepcopy(self.snapshot)

    def test_real_coverage_and_candidates(self):
        snapshot = self.snapshot
        self.assertEqual(snapshot["version"], "0.2.0")
        self.assertEqual(len(snapshot["coverage"]["groups"]), 14)
        self.assertEqual(snapshot["coverage"]["message_count"], 801)
        self.assertEqual(snapshot["coverage"]["media"], {
            "images_requested": 204, "images_exported": 158,
            "voices_requested": 7, "voices_exported": 7, "voice_transcripts": 7,
            "videos_requested": 26, "videos_exported": 18,
            "files_requested": 9, "files_exported": 9,
        })
        self.assertEqual(len(snapshot["items"]), 18)
        self.assertEqual(Counter(item["category"] for item in snapshot["items"]),
                         {"资料": 6, "问题": 6, "任务": 1, "确认": 5})
        self.assertTrue(all(item["review_state"] == REVIEW_PENDING for item in snapshot["items"]))

    def test_ids_stable_across_rebuilds(self):
        rebuilt = build_bootstrap_snapshot(WORKSPACE)
        self.assertEqual([item["id"] for item in self.snapshot["items"]],
                         [item["id"] for item in rebuilt["items"]])

    def test_only_direct_stage_confirmations_close(self):
        closed = {item["id"]: item for item in self.snapshot["items"] if item["status"] == "已解决"}
        self.assertEqual(set(closed), {"SAMPLE-P03-002", "REMAINING-004", "REMAINING-007", "REMAINING-008"})
        self.assertEqual(closed["SAMPLE-P03-002"]["closed_at"], "2026-10-09T09:50:25+08:00")
        self.assertNotEqual(closed["SAMPLE-P03-002"]["closed_at"], closed["SAMPLE-P03-002"]["last_seen"])
        self.assertEqual(closed["REMAINING-004"]["closed_at"], "2026-10-12T14:12:27+08:00")
        self.assertEqual(closed["REMAINING-007"]["closed_at"], "2026-10-09T13:36:09+08:00")
        self.assertEqual(closed["REMAINING-008"]["closed_at"], "2026-10-09T11:18:36+08:00")
        self.assertIn("不含结构安全验证", closed["REMAINING-008"]["closure_scope"])
        self.assertIn("不含干燥完成、邮寄或交付", closed["REMAINING-007"]["closure_scope"])

    def test_owners_and_deadlines_not_inferred(self):
        assigned = {item["id"]: item["owner"] for item in self.snapshot["items"] if item["owner"]}
        self.assertEqual(assigned, {"SAMPLE-P03-003": "李婷"})
        self.assertTrue(all(item["deadline"] is None for item in self.snapshot["items"]))
        self.assertIn("李睿琪，曾悦", self.by_id["FILE-REVIEW-001"]["summary"])
        self.assertIn("具体执行人未明", self.by_id["REMAINING-005"]["summary"])

    def test_similar_eggs_kept_in_distinct_groups(self):
        blocked = self.by_id["REMAINING-002"]
        passed = self.by_id["REMAINING-007"]
        self.assertEqual(blocked["source_id"], "production_09")
        self.assertEqual(blocked["status"], "待确认")
        self.assertEqual(passed["source_id"], "production_04")
        self.assertEqual(passed["status"], "已解决")

    def test_duplicate_primary_key_rejected(self):
        snapshot = self.mutated()
        snapshot["items"].append(copy.deepcopy(snapshot["items"][0]))
        with self.assertRaisesRegex(ValueError, "item.id重复"):
            validate_snapshot(snapshot)

    def test_missing_group_and_wrong_group_rejected(self):
        snapshot = self.mutated()
        snapshot["coverage"]["groups"].pop()
        with self.assertRaisesRegex(ValueError, "共14群"):
            validate_snapshot(snapshot)
        snapshot = self.mutated()
        snapshot["items"][0]["group"] = "别的群"
        with self.assertRaisesRegex(ValueError, "映射无效"):
            validate_snapshot(snapshot)

    def test_bad_state_rejected(self):
        snapshot = self.mutated()
        snapshot["items"][0]["status"] = "已经差不多"
        with self.assertRaisesRegex(ValueError, "status无效"):
            validate_snapshot(snapshot)

    def test_invalid_or_naive_evidence_time_rejected(self):
        for value in ("2026-99-99T00:00:00+08:00", "2026-10-12 14:52:05"):
            snapshot = self.mutated()
            snapshot["items"][0]["evidence"][0]["time"] = value
            with self.assertRaisesRegex(ValueError, "日期时间无效|必须包含时区"):
                validate_snapshot(snapshot)

    def test_cross_group_or_incomplete_locator_rejected(self):
        for value in ("production_03|message\\message_3.db:29", "message\\message_3.db:29"):
            snapshot = self.mutated()
            snapshot["items"][0]["evidence"][0]["locator"] = value
            with self.assertRaisesRegex(ValueError, "locator无效或跨群"):
                validate_snapshot(snapshot)

    def test_unreviewed_unregistered_closure_rejected(self):
        snapshot = self.mutated()
        item = snapshot["items"][0]
        item["status"] = "已解决"
        item["closed_at"] = item["last_seen"]
        with self.assertRaisesRegex(ValueError, "尚未副厂复核且未登记"):
            validate_snapshot(snapshot)

    def test_unknown_review_state_cannot_bypass_closure_check(self):
        snapshot = self.mutated()
        item = snapshot["items"][0]
        item["status"] = "已解决"
        item["closed_at"] = item["last_seen"]
        item["review_state"] = "随便填写"
        with self.assertRaisesRegex(ValueError, "尚未副厂复核且未登记"):
            validate_snapshot(snapshot)

    def test_missing_nullable_field_is_still_invalid_schema(self):
        snapshot = self.mutated()
        del snapshot["items"][0]["owner"]
        with self.assertRaisesRegex(ValueError, "item缺少字段：owner"):
            validate_snapshot(snapshot)

    def test_closed_at_cannot_use_last_acknowledgment(self):
        snapshot = self.mutated()
        item = next(item for item in snapshot["items"] if item["id"] == "SAMPLE-P03-002")
        item["closed_at"] = item["last_seen"]
        with self.assertRaisesRegex(ValueError, "关闭范围或时间"):
            validate_snapshot(snapshot)

    def test_stage_scope_cannot_expand_to_finished_product(self):
        snapshot = self.mutated()
        item = next(item for item in snapshot["items"] if item["id"] == "REMAINING-007")
        item["closure_scope"] = "恐龙蛋全部完工并邮寄"
        with self.assertRaisesRegex(ValueError, "关闭范围或时间"):
            validate_snapshot(snapshot)

    def test_media_sum_mismatch_rejected(self):
        snapshot = self.mutated()
        snapshot["coverage"]["media"]["images_exported"] = 204
        with self.assertRaisesRegex(ValueError, "images_exported与14群合计不一致"):
            validate_snapshot(snapshot)

    def test_bom_json_supported_and_error_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "带BOM.json"
            path.write_text(json.dumps({"说明": "中文"}, ensure_ascii=False), encoding="utf-8-sig")
            self.assertEqual(_read_json(path), {"说明": "中文"})
            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "无法读取JSON.*带BOM.json"):
                _read_json(path)


if __name__ == "__main__":
    unittest.main()
