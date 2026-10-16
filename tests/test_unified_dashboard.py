"""全部虚构数据；验证优先计时、售后人工结果、权限和任务失败不改台账。"""
import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.after_sales import AfterSales
from app.jobs import Jobs, ACTIVE, check_manifest, file_lock, verify_candidates, write_json, run_job, codex_options
from app.store import Store, TZ
from app.workdays import priority, is_workday
from tests.test_store_server import StoreServerTests, synthetic_snapshot


def ledger():
    return {"version": "V1.0.0", "period": "2026-09", "cases": [
        {"id": "SYN-1", "company": "大洋", "include_in_total": True, "status": "已给建议",
         "observed": "虚构现象", "next_action": "虚构下一步", "evidence": [{"source_id": "dayang", "source_ref": "message\\message_1.db:1"}]}]}


class WorkdayTests(unittest.TestCase):
    def test_weekends_are_factory_workdays(self):
        self.assertTrue(is_workday(datetime(2026, 10, 11).date()))
        item = {"status": "待处理", "first_seen": "2026-10-09T15:00:00+08:00"}
        self.assertFalse(priority(item, datetime(2026, 10, 12, 14, 59, tzinfo=TZ))["urgent"])
        self.assertTrue(priority(item, datetime(2026, 10, 12, 15, tzinfo=TZ))["urgent"])

    def test_holiday_excluded_and_saturday_included(self):
        item = {"status": "待处理", "first_seen": "2026-09-30T10:00:00+08:00"}
        self.assertEqual(priority(item, datetime(2026, 10, 9, 10, tzinfo=TZ))["workdays"], 2)
        self.assertTrue(priority(item, datetime(2026, 10, 10, 10, tzinfo=TZ))["urgent"])

    def test_closed_pending_merge_and_unknown_year(self):
        for status in ["已解决", "完成验收", "待并案"]:
            self.assertFalse(priority({"status": status, "first_seen": "2026-09-01"})["urgent"])
        result = priority({"status": "待处理", "first_seen": "2026-12-30"}, datetime(2027, 1, 5, tzinfo=TZ))
        self.assertIsNone(result["workdays"])
        self.assertFalse(result["urgent"])


class SalesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.sales = AfterSales(self.store)
        self.sales.import_ledger(ledger(), "0" * 64)

    def test_reimport_preserves_manual_and_timer(self):
        original = self.sales.snapshot()["cases"][0]
        actor = {"username": "liruiqi", "display_name": "李睿琪", "role": "admin"}
        self.sales.update(original["id"], {"status": "完成验收", "progress_note": "虚构现场验收说明"}, actor)
        self.sales.import_ledger(ledger(), "1" * 64)
        new = self.sales.snapshot()["cases"][0]
        self.assertEqual(new["status"], "完成验收")
        self.assertEqual(original["tracking_started_at"], new["tracking_started_at"])
        self.assertFalse(new["priority"]["urgent"])

    def test_owner_only_does_not_reset(self):
        original = self.sales.snapshot()["cases"][0]
        actor = {"username": "liruiqi", "display_name": "李睿琪", "role": "admin"}
        self.sales.update(original["id"], {"owner": "张英"}, actor)
        self.assertNotIn("progress_at", self.sales.snapshot()["cases"][0])
        with self.assertRaises(ValueError):
            self.sales.update(original["id"], {"status": "完成验收"}, actor)

    def test_invalid_import_is_atomic(self):
        bad = ledger(); bad["cases"].append({**bad["cases"][0], "id": "SYN-2", "status": "维修好了"})
        with self.assertRaises(ValueError):
            self.sales.import_ledger(bad, "2" * 64)
        self.assertEqual(len(self.sales.snapshot()["cases"]), 1)

    def test_production_owner_only_and_progress_note(self):
        self.store.import_snapshot(synthetic_snapshot())
        result = self.store.update_item("SYNTHETIC-001", {"owner": "郭逸"}, "liruiqi")
        self.assertNotIn("progress_at", result)
        result = self.store.update_item("SYNTHETIC-001", {"closure_note": "虚构实质结果"}, "liruiqi")
        self.assertIn("progress_at", result)
        self.assertEqual(len(result["progress_notes"]), 1)

    def test_approved_stage_is_not_reopened_by_candidate(self):
        original = synthetic_snapshot()
        item = original["items"][0]
        item.update(status="已解决", closed_at=item["last_seen"], review_state="已复核", closure_scope="虚构已完成阶段")
        self.store.import_snapshot(original)
        self.store.import_snapshot(synthetic_snapshot())
        result = self.store.snapshot()["items"][0]
        self.assertEqual(result["status"], "已解决")
        self.assertEqual(result["closure_scope"], "虚构已完成阶段")

    def test_cross_period_explicit_link(self):
        next_month = ledger();next_month["period"] = "2026-10"
        next_month["cases"][0]["historical_case_id"] = "2026-09:大洋:SYN-1"
        self.sales.import_ledger(next_month, "3" * 64)
        data = self.sales.snapshot()
        self.assertEqual(len(data["cases"]), 1)
        self.assertEqual(data["cases"][0]["period"], "2026-09")


class JobTests(unittest.TestCase):
    def test_model_selection_without_loading_external_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.toml").write_text('model="synthetic-model"\nmodel_reasoning_effort="high"\n[mcp_servers.synthetic]\ncommand="must-not-start"\n', encoding="utf-8")
            with patch.dict("os.environ", {"CODEX_HOME": tmp}):
                options = codex_options()
            self.assertIn('--ignore-user-config', options)
            self.assertIn('model="synthetic-model"', options)
            self.assertFalse(any('must-not-start' in arg for arg in options))

    def test_daily_reasoning_cap_does_not_edit_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "config.toml")
            original = 'model="synthetic-model"\nmodel_reasoning_effort="ultra"\n'
            path.write_text(original, encoding="utf-8")
            with patch.dict("os.environ", {"CODEX_HOME": tmp}):
                options = codex_options()
            self.assertIn('model_reasoning_effort="high"', options)
            self.assertEqual(path.read_text("utf-8"), original)

    def test_worker_full_flow_with_synthetic_sources(self):
        from types import SimpleNamespace
        from app.normalize import SOURCE_GROUPS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root)
            snapshot = synthetic_snapshot()
            jobs = Jobs(root);job_id = "1" * 32
            write_json(jobs.path, {"id": job_id, "state": "queued", "started_at": datetime.now(TZ).isoformat(), "requested_by": "synthetic"})
            (jobs.path.parent / job_id).mkdir()
            def fake_subprocess(args, **kwargs):
                if "--scope" in args:
                    scope = args[args.index("--scope") + 1]
                    folder = Path(args[args.index("--out") + 1])
                    ids = SOURCE_GROUPS if scope == "production" else {"dayang": "虚构售后1", "yunyi": "虚构售后2", "jinnai": "虚构售后3"}
                    results = [{"source_id": key, "group": name, "status": "verified_empty_range", "message_count": 0} for key,name in ids.items()]
                    write_json(folder / "collection_manifest_V0.1.3.json", {"status": "exported", "sensitive_cache_cleanup": True, "start_inclusive": snapshot["period"]["start"], "end_exclusive": snapshot["period"]["end"], "export_results": results})
                else:
                    target = Path(args[args.index("--output-last-message") + 1])
                    write_json(target, {"items": [], "after_sales_cases": [], "limitations": ["虚构空范围验收"]})
                return SimpleNamespace(returncode=0)
            with patch("app.jobs.subprocess.run", side_effect=fake_subprocess), patch("app.jobs.codex_executable", return_value="synthetic-codex"), patch("app.jobs.codex_options", return_value=[]):
                self.assertEqual(run_job(root, job_id), 0)
            self.assertEqual(jobs.status()["state"], "completed")
            self.assertTrue((jobs.path.parent / job_id / "daily.html").is_file())
            self.assertEqual(store.snapshot()["coverage"]["message_count"], 0)

    def test_queue_blocks_duplicates_and_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            write_json(jobs.path, {"id": "0" * 32, "state": "queued", "pid": None, "started_at": datetime.now(TZ).isoformat()})
            with self.assertRaisesRegex(ValueError, "已有整理"):
                jobs.start("synthetic")
            self.assertEqual(jobs.status()["state"], "queued")

    def test_stopped_worker_not_reported_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(tmp)
            write_json(jobs.path, {"id": "0" * 32, "state": "analyzing", "pid": 0, "started_at": "2026-01-01T00:00:00+08:00"})
            self.assertEqual(jobs.status()["state"], "failed")

    def test_lock_contention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            with file_lock(path):
                with self.assertRaises(ValueError):
                    with file_lock(path):
                        pass

    def test_manifest_hash_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            raw = Path(tmp) / "raw.json";raw.write_text('{}')
            write_json(path, {"status": "exported", "sensitive_cache_cleanup": True, "export_results": [{"source_id": "dayang", "source_json": str(raw), "source_sha256": "0" * 64}]})
            with self.assertRaises(ValueError):
                check_manifest(path, {"dayang"})


class UnifiedApiTests(StoreServerTests):
    def test_new_routes_readonly_and_worker_authorization(self):
        login = self.store.login("zengyue", self.password)
        headers = {"Cookie": "dino_session=" + login["token"], "X-CSRF-Token": login["csrf"]}
        status, _, _ = self.request("GET", "/api/v1/after-sales", headers=headers)
        self.assertEqual(status, 200)
        status, _, _ = self.request("POST", "/api/jobs", {}, headers)
        self.assertEqual(status, 403)
        login = self.store.login("liruiqi", self.password)
        headers = {"Cookie": "dino_session=" + login["token"], "X-CSRF-Token": login["csrf"]}
        with patch.object(self.server.jobs, "start", return_value={"state": "queued"}) as start:
            status, _, _ = self.request("POST", "/api/jobs", {}, headers)
            self.assertEqual(status, 202)
            self.assertEqual(start.call_count, 1)
            status, _, _ = self.request("POST", "/api/jobs", {"command": "unsafe"}, headers)
            self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
