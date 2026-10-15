"""虚构沟通资料与消息，验证私有接入和简短放行的边界。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.communication import FILENAME, MAX_BYTES, explicit_short_reply_context, load_communication_profile
from app.jobs import analysis_input
from app.jobs import Jobs, run_job, write_json


OFFICE = {"names": ["测试人员甲"], "aliases": {"测试甲": "测试人员甲"}}


def profile():
    return {"schema": 1, "version": "1.0.0", "updated_at": "2026-10-15T20:00:00+08:00",
            "requested_period": {"start": "2025-10-16T00:00:00+08:00", "end": "2026-10-15T19:00:00+08:00"},
            "observed_period": {"start": "2026-05-01T00:00:00+08:00", "end": "2026-10-15T18:00:00+08:00"},
            "coverage_note": "合成测试资料，非完整一年历史。",
            "speakers": [{"name": "测试人员甲", "aliases": ["测试甲"], "identity_sha256": "1" * 64, "notes": "仅合成语言参考。"}],
            "rules": ["明确阶段问答可以支持口头放行。"],
            "examples": [{"speaker": "测试人员甲", "source_id": "production_01", "refs": ["production_01|message\\message_1.db:1"],
                          "context": "虚构造型可以下一步吗？好。", "meaning": "只限造型放行。", "boundary": "不能推断整件完成。"}]}


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "private").mkdir()
        self.path = self.root / "private" / FILENAME

    def save(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_absent_compatible_and_no_history_added_to_messages(self):
        self.assertEqual(load_communication_profile(self.root, OFFICE), (None, {"loaded": False}))
        empty = {"export_results": []}
        data = analysis_input(empty, empty, {})
        self.assertNotIn("communication_profile", data)
        self.save(profile())
        value, receipt = load_communication_profile(self.root, OFFICE)
        data = analysis_input(empty, empty, {}, value)
        self.assertEqual(data["communication_profile"], value)
        self.assertEqual(data["production"], [])
        self.assertTrue(receipt["loaded"])
        self.assertEqual(receipt["example_count"], 1)
        self.assertEqual(len(receipt["sha256"]), 64)

    def test_each_run_reads_current_file_independent_of_snapshot_import(self):
        self.save(profile())
        one, receipt_one = load_communication_profile(self.root, OFFICE)
        two = copy.deepcopy(one)
        two["rules"].append("新的合成边界。")
        self.save(two)
        current, receipt_two = load_communication_profile(self.root, OFFICE)
        self.assertEqual(current, two)
        self.assertNotEqual(receipt_one["sha256"], receipt_two["sha256"])

    def test_worker_transmits_profile_and_archives_provenance(self):
        from app.normalize import SOURCE_GROUPS
        from tests.test_store_server import synthetic_snapshot
        self.save(profile())
        write_json(self.root / "private/office_staff.json", OFFICE)
        jobs = Jobs(self.root); job_id = "2" * 32
        write_json(jobs.path, {"id": job_id, "state": "queued", "started_at": profile()["updated_at"]})
        (jobs.path.parent / job_id).mkdir()
        transmitted = []
        def process(args, **kwargs):
            if "--scope" in args:
                scope = args[args.index("--scope") + 1]
                folder = Path(args[args.index("--out") + 1])
                sources = SOURCE_GROUPS if scope == "production" else {"dayang": "虚构售后1", "yunyi": "虚构售后2", "jinnai": "虚构售后3"}
                period = synthetic_snapshot()["period"]
                write_json(folder / "collection_manifest_V0.1.3.json", {"status": "exported", "sensitive_cache_cleanup": True,
                           "start_inclusive": period["start"], "end_exclusive": period["end"],
                           "export_results": [{"source_id": key, "group": name, "status": "verified_empty_range", "message_count": 0} for key, name in sources.items()]})
            else:
                prompt = kwargs["input"].decode("utf-8")
                data = json.loads(prompt.split("\nDATA=", 1)[1])
                transmitted.append(data["communication_profile"])
                self.assertEqual(data["production"][0]["messages"], [])
                write_json(Path(args[args.index("--output-last-message") + 1]), {"items": [], "after_sales_cases": [], "limitations": ["合成空范围"]})
            return SimpleNamespace(returncode=0)
        with patch("app.jobs.subprocess.run", side_effect=process), patch("app.jobs.codex_executable", return_value="synthetic"), patch("app.jobs.codex_options", return_value=[]):
            self.assertEqual(run_job(self.root, job_id), 0)
        self.assertEqual(transmitted, [profile()])
        receipt = json.loads((jobs.path.parent / job_id / "communication_profile_receipt.json").read_text("utf-8"))
        self.assertTrue(receipt["loaded"])
        self.assertEqual(jobs.status()["state"], "completed")

    def test_invalid_config_rejected_without_contents_in_error(self):
        cases = []
        for field, value in [("schema", 2), ("rules", []), ("examples", []), ("requested_period", {"start": "invalid", "end": "invalid"})]:
            item = profile(); item[field] = value; cases.append(item)
        item = profile();item["speakers"][0]["name"] = "非办公室人员";cases.append(item)
        item = profile();item["speakers"][0]["aliases"] = ["陌生人"];cases.append(item)
        item = profile();item["examples"][0]["refs"] = ["production_02|message\\message_1.db:1"];cases.append(item)
        item = profile();item["observed_period"]["start"] = "2020-01-01T00:00:00+08:00";cases.append(item)
        item = profile();item["instructions"] = "此处为应被拒绝的多余字段";cases.append(item)
        for value in cases:
            with self.subTest(value=value):
                self.save(value)
                with self.assertRaisesRegex(ValueError, "本机沟通理解资料无效") as error:
                    load_communication_profile(self.root, OFFICE)
                self.assertNotIn("陌生人", str(error.exception))
        self.path.write_bytes(b"x" * (MAX_BYTES + 1))
        with self.assertRaises(ValueError):load_communication_profile(self.root, OFFICE)

    def test_contextual_agreement_and_counterexamples(self):
        question = {"locator": "production_01|message\\message_1.db:1", "time": "2026-10-15T09:00:00+08:00", "sender": "测试甲", "text": "这个造型可以下一步吗？"}
        reply = {"locator": "production_01|message\\message_1.db:2", "time": "2026-10-15T09:01:00+08:00", "sender": "测试乙", "text": "好"}
        self.assertTrue(explicit_short_reply_context(reply, [question, reply]))
        self.assertFalse(explicit_short_reply_context(reply, [reply]))
        for changes in [{"text": "造型视频发给你了"}, {"text": "[图片]"}, {"text": "明天安排师傅做"},
                        {"sender": "测试乙"}, {"time": "2026-10-15T08:00:00+08:00"}, {"time": "2026-10-15T09:02:00+08:00"},
                        {"locator": "production_02|message\\message_1.db:1"}]:
            self.assertFalse(explicit_short_reply_context(reply, [{**question, **changes}, reply]))


if __name__ == "__main__":
    unittest.main()
