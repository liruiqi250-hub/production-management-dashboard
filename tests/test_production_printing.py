"""生产打印队列测试：全部为临时库、合成 PDF 和 mock runner，绝不向设备打印。"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from pypdf import PdfWriter

from app.production_printing import ProductionPrinting, PRINTER_NAME, parse_pages
from app.production import Production
from app.store import Store
from scripts.print_production_pdf import validate_manifest


class FakeDocuments:
    def __init__(self, root):
        self.path = root / "private/synthetic-source.pdf"
        self.held = 0
        self.holds = 0
        self.write(5)

    def write(self, count):
        writer = PdfWriter()
        for _ in range(count):
            writer.add_blank_page(width=120, height=60)
        writer.write(self.path)

    @contextmanager
    def hold(self, output_id):
        self.held += 1
        self.holds += 1
        try:
            yield
        finally:
            self.held -= 1

    def file(self, output_id, format):
        assert self.held and format == "pdf", "必须在原件保护锁中复制 PDF"
        return self.path, "虚构打印文件.pdf", "application/pdf"


class ProductionPrintingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-print-queue-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.production = Production(self.store)
        order = self.production.add_order({"title": "虚构打印边界测试"}, "liruiqi")["id"]
        self.production.add_piece({"order_id": order, "piece_label": "虚构1", "product_name": "虚构产品"}, "liruiqi")
        self.production_before = self.production.snapshot()
        self.store.create_user("synthetic_viewer", "虚构查看人员", "viewer")
        self.store.create_user("synthetic_other", "虚构其他人员", "viewer")
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET must_change_password=0")
        self.actor = {"username": "synthetic_viewer", "auth_type": "session"}
        self.documents = FakeDocuments(self.root)
        self.runner = Mock(return_value={"state": "submitted"})
        self.printing = self.make_queue(self.runner)
        self.output_id = "a" * 32
        self.config = self.root / "private/production_printing.json"
        self.config.write_text(json.dumps({"schema": 1, "enabled": True, "printer_name": PRINTER_NAME}), encoding="utf-8")

    def tearDown(self):
        self.assertEqual(self.production.snapshot(), self.production_before)

    def make_queue(self, runner):
        queue = ProductionPrinting(self.store, self.documents, runner=runner)
        queue._tools = Mock(return_value=(Path("synthetic-lo-python"), Path("synthetic-helper")))
        queue._printer = Mock(return_value={"name": PRINTER_NAME, "status": "Normal", "port": "synthetic-port",
                                           "host_address": "192.168.253.253"})
        self.addCleanup(queue.stop)
        return queue

    def submit(self, **kwargs):
        return self.printing.submit(self.output_id, self.actor, request_id=kwargs.pop("request_id", str(uuid.uuid4())), **kwargs)

    def wait_terminal(self, queue, job_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = queue.get(job_id, self.actor)
            if job["state"] in {"submitted", "failed", "unknown"}:
                return job
            time.sleep(0.01)
        self.fail("合成打印任务未在测试期限内完成")

    def test_page_ranges_are_precise_sorted_unique_and_bounded(self):
        self.assertEqual(parse_pages("5,1-3,2", 5), [1, 2, 3, 5])
        self.assertEqual(parse_pages("1；3，5", 5), [1, 3, 5])
        self.assertEqual(parse_pages("", 3), [1, 2, 3])
        for value in (None, [], "0", "-1", "01", "3-1", "6", "1,,2", "1-2-3", "all", "1,", "1/2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_pages(value, 5)

    def test_status_is_read_safe_disabled_or_fixed_target(self):
        status = self.printing.status()
        self.assertTrue(status["enabled"] and status["available"])
        self.assertEqual(status["host_address"], "192.168.253.253")
        self.runner.assert_not_called()
        self.config.unlink()
        self.assertFalse(self.printing.status()["enabled"])
        self.config.write_text(json.dumps({"schema": 1, "enabled": True, "printer_name": "unapproved"}), encoding="utf-8")
        self.assertFalse(self.printing.status()["available"])
        self.runner.assert_not_called()

    def test_viewer_can_queue_and_private_pdf_is_verified_held_copy(self):
        job = self.submit(pages="1-2,5", copies=2)
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["pages"], [1, 2, 5])
        self.assertEqual(job["total_sheets"], 6)
        self.assertNotIn("payload_sha256", job)
        self.assertNotIn("username", job)
        payload = self.printing.base / job["id"] / "payload.pdf"
        self.assertEqual(payload.read_bytes(), self.documents.path.read_bytes())
        self.assertEqual(self.documents.holds, 1)
        self.assertEqual(self.printing.active_output_ids(), {self.output_id})
        manifest = payload.with_name("manifest.json")
        record, actual = validate_manifest(self.root, manifest)
        self.assertEqual(record["pages"], [1, 2, 5])
        self.assertEqual(actual, payload)
        self.runner.assert_not_called()

    def test_api_inactive_initial_password_and_other_owner_access_are_denied(self):
        with self.assertRaises(PermissionError):
            self.printing.submit(self.output_id, {**self.actor, "auth_type": "api"}, request_id=str(uuid.uuid4()))
        job = self.submit()
        with self.assertRaises(PermissionError):
            self.printing.get(job["id"], {"username": "synthetic_other", "auth_type": "session"})
        admin = self.printing.get(job["id"], {"username": "liruiqi", "auth_type": "session"})
        self.assertEqual(admin["id"], job["id"])
        for change in ("active=0", "active=1,must_change_password=1"):
            with self.store.connect() as conn:
                conn.execute("UPDATE users SET " + change + " WHERE username='synthetic_viewer'")
            with self.assertRaises(PermissionError):
                self.submit()

    def test_idempotency_is_per_user_and_conflicting_reuse_is_rejected(self):
        request_id = str(uuid.uuid4())
        first = self.submit(request_id=request_id, pages="1-2")
        self.assertEqual(first["id"], self.submit(request_id=request_id, pages="2,1")["id"])
        self.assertEqual(self.documents.holds, 1)
        with self.assertRaises(ValueError):
            self.submit(request_id=request_id, pages="3")
        second = self.printing.submit(self.output_id, {"username": "synthetic_other", "auth_type": "session"}, pages="1-2", request_id=request_id)
        self.assertNotEqual(first["id"], second["id"])

    def test_limits_reject_invalid_copies_and_more_than_250_sheets(self):
        for copies in (True, 0, 6, 1.5, "1"):
            with self.subTest(copies=copies), self.assertRaises(ValueError):
                self.submit(copies=copies)
        self.documents.write(60)
        with self.assertRaisesRegex(ValueError, "250"):
            self.submit(copies=5)
        self.assertEqual(self.submit(copies=5, pages="1-50")["total_sheets"], 250)
        with self.assertRaises(ValueError):
            self.submit(request_id="not-a-uuid")

    def test_worker_is_serial_and_removes_only_verified_payload_after_submit(self):
        entered, release = threading.Event(), threading.Event()
        active = [0, 0]
        lock = threading.Lock()

        def runner(manifest):
            with lock:
                active[0] += 1
                active[1] = max(active[1], active[0])
            entered.set()
            release.wait(2)
            with lock:
                active[0] -= 1
            return {"state": "submitted"}

        self.printing._runner = Mock(side_effect=runner)
        jobs = [self.submit(pages="1"), self.submit(pages="2")]
        self.printing.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.printing.active_output_ids(), {self.output_id})
        self.assertEqual(self.printing._runner.call_count, 1)
        release.set()
        for job in jobs:
            result = self.wait_terminal(self.printing, job["id"])
            self.assertEqual(result["state"], "submitted")
            self.assertIn("不代表", result["message"])
        self.assertEqual(active[1], 1)
        self.printing.stop()
        self.assertEqual(self.printing.active_output_ids(), set())
        self.assertTrue(self.documents.path.is_file())
        for job in jobs:
            folder = self.printing.base / job["id"]
            self.assertFalse((folder / "payload.pdf").exists())
            self.assertTrue((folder / "manifest.json").exists())

    def test_queued_jobs_recover_but_interrupted_submitting_is_unknown_not_reprinted(self):
        queued = self.submit(pages="1")
        interrupted = self.submit(pages="2")
        data = self.printing._get(interrupted["id"])
        data["state"] = "submitting"
        with self.store.connect() as conn:
            conn.execute("UPDATE production_print_jobs SET state='submitting',data_json=? WHERE id=?", (json.dumps(data), data["id"]))
        new_runner = Mock(return_value={"state": "submitted"})
        recovered = self.make_queue(new_runner)
        recovered.start()
        self.assertEqual(self.wait_terminal(recovered, queued["id"])["state"], "submitted")
        self.assertEqual(recovered.get(interrupted["id"], self.actor)["state"], "unknown")
        self.assertEqual(new_runner.call_count, 1)
        self.assertFalse((recovered.base / interrupted["id"] / "payload.pdf").exists())

    def test_failed_and_uncertain_results_are_terminal_and_not_retried(self):
        self.printing._runner = Mock(side_effect=[{"state": "failed"}, RuntimeError("synthetic interruption")])
        first, second = self.submit(), self.submit()
        self.printing.start()
        self.assertEqual(self.wait_terminal(self.printing, first["id"])["state"], "failed")
        self.assertEqual(self.wait_terminal(self.printing, second["id"])["state"], "unknown")
        self.printing.stop()
        self.assertEqual(self.printing._runner.call_count, 2)
        self.printing.start()
        time.sleep(0.05)
        self.assertEqual(self.printing._runner.call_count, 2)

    def test_tampered_copy_is_not_printed_or_deleted_as_verified_payload(self):
        job = self.submit()
        payload = self.printing.base / job["id"] / "payload.pdf"
        payload.write_bytes(b"tampered synthetic")
        self.printing.start()
        self.assertEqual(self.wait_terminal(self.printing, job["id"])["state"], "failed")
        self.runner.assert_not_called()
        self.assertTrue(payload.exists())
        self.assertTrue(self.documents.path.exists())

    def test_helper_manifest_rejects_paths_commands_hash_and_range_tampering(self):
        job = self.submit()
        path = self.printing.base / job["id"] / "manifest.json"
        original = json.loads(path.read_text("utf-8"))
        for change in ({"payload": "../synthetic-source.pdf"}, {"printer_name": "unapproved"},
                       {"copies": 6}, {"pages": [0]}, {"pages": [1, 1]}, {"payload_sha256": "0" * 64},
                       {"command": "must never run"}):
            with self.subTest(change=change):
                data = {**original, **change}
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    validate_manifest(self.root, path)
        with self.assertRaises(ValueError):
            validate_manifest(self.root, self.documents.path)

    def test_disabling_account_after_queue_prevents_runner(self):
        job = self.submit()
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET active=0 WHERE username='synthetic_viewer'")
        self.printing.start()
        deadline = time.monotonic() + 3
        while self.printing._get(job["id"])["state"] == "queued" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.printing.stop()
        self.assertEqual(self.printing._get(job["id"])["state"], "failed")
        self.runner.assert_not_called()

    def test_partial_copy_failure_leaves_no_orphan_payload_or_database_job(self):
        original = self.documents.path.read_bytes()

        def fail_copy(source, target, **kwargs):
            target.write(source.read(20))
            raise OSError("synthetic disk failure")

        with patch("app.production_printing.shutil.copyfileobj", side_effect=fail_copy):
            with self.assertRaisesRegex(ValueError, "副本准备失败"):
                self.submit()
        self.assertEqual(list(self.printing.base.glob("*/payload.pdf")), [])
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM production_print_jobs").fetchone()[0], 0)
        self.assertEqual(self.documents.path.read_bytes(), original)
        self.runner.assert_not_called()

    def test_default_runner_remains_mockable_after_queue_construction(self):
        queue = self.make_queue(None)
        job = queue.submit(self.output_id, self.actor, request_id=str(uuid.uuid4()))
        with patch.object(ProductionPrinting, "_run_helper", return_value={"state": "submitted"}) as helper:
            queue.start()
            self.assertEqual(self.wait_terminal(queue, job["id"])["state"], "submitted")
            helper.assert_called_once_with(queue.base / job["id"] / "manifest.json")
            queue.stop()


if __name__ == "__main__":
    unittest.main()
