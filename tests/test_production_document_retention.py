"""再生资料七日清理：全部使用临时目录、合成文件和隔离数据库。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from app.production_documents import ArtifactsExpired, ProductionDocuments
from app.store import Store, TZ


class ProductionDocumentRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="document-retention-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.documents = ProductionDocuments(self.store)
        self.addCleanup(self.documents.stop_maintenance)
        self.reference = datetime.now(TZ)
        self.old = (self.reference - timedelta(days=8)).isoformat(timespec="seconds")
        self.source = self.store.private / "production_order_sources" / "原生产单.xlsx"
        self.template = self.store.private / "production_templates" / "product.xlsx"
        for path in (self.source, self.template):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"must-remain")

    def output(self, *, age=None, status="ready", pages=1):
        from pypdf import PdfWriter
        output_id = uuid.uuid4().hex
        folder = self.documents.base / output_id
        folder.mkdir(parents=True)
        xlsx, pdf = folder / "合成资料.xlsx", folder / "合成资料.pdf"
        xlsx.write_bytes(b"PK\x03\x04synthetic-xlsx")
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=100, height=120)
        writer.write(str(pdf))
        record = {"id": output_id, "order_id": "SYNTHETIC-ORDER", "document_id": "SYNTHETIC-SOURCE",
                  "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(), "kind": "product", "batch": 1,
                  "created_at": self.old, "last_used_at": age or self.old, "filename": xlsx.name,
                  "status": status, "pdf_ready": True, "preview_ready": True, "pages": pages,
                  "warnings": [], "files": {"xlsx": self.documents._record_file(xlsx), "pdf": self.documents._record_file(pdf)}}
        with self.store.connect() as conn:
            conn.execute("INSERT INTO documents_generated VALUES(?,?,?,?,?,?)", (output_id, record["order_id"], "product", 1, self.old, json.dumps(record, ensure_ascii=False)))
        return output_id, folder, record

    def expire(self, output_id):
        record = self.documents._get(output_id)
        record["last_used_at"] = self.old
        # 模拟旧版本数据库时间，故意绕过防止回退时间的_save。
        with self.store.connect() as conn:
            conn.execute("UPDATE documents_generated SET data_json=? WHERE id=?", (json.dumps(record), output_id))

    def test_seven_day_cleanup_preserves_source_template_database_and_history(self):
        output_id, folder, before = self.output()
        result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 1)
        self.assertEqual(result["status"], "completed")
        self.assertGreater(result["bytes_freed"], 0)
        self.assertFalse(folder.exists())
        after = self.documents._get(output_id)
        self.assertEqual(after["status"], "cleaned")
        self.assertEqual(after["source_sha256"], before["source_sha256"])
        self.assertEqual(after["document_id"], before["document_id"])
        self.assertTrue(after["cleaned_at"])
        self.assertEqual(self.source.read_bytes(), b"must-remain")
        self.assertEqual(self.template.read_bytes(), b"must-remain")
        self.assertTrue(self.store.path.is_file())
        history = self.documents.status("SYNTHETIC-ORDER")["history"]
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["artifacts_available"])
        with self.assertRaises(ArtifactsExpired):
            self.documents.file(output_id, "xlsx")
        with self.assertRaises(ArtifactsExpired):
            self.documents.preview_page(output_id, 1)
        again = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(again["cleaned"], 0)
        receipt = json.loads(self.documents.cleanup_receipt.read_text("utf-8"))
        self.assertEqual(receipt["cleaned"], 0)

    def test_status_does_not_touch_but_successful_download_does(self):
        output_id, folder, _ = self.output()
        self.documents.status("SYNTHETIC-ORDER")
        self.assertEqual(self.documents._get(output_id)["last_used_at"], self.old)
        path, _, _ = self.documents.file(output_id, "xlsx")
        self.assertTrue(path.is_file())
        self.assertGreater(datetime.fromisoformat(self.documents._get(output_id)["last_used_at"]), self.reference - timedelta(days=1))
        self.assertEqual(self.documents.cleanup_expired(now=self.reference)["cleaned"], 0)
        self.assertTrue(folder.exists())

    def test_unsuccessful_file_or_preview_does_not_extend_retention(self):
        output_id, folder, _ = self.output()
        (folder / "合成资料.xlsx").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.documents.file(output_id, "xlsx")
        with self.assertRaises(ValueError):
            self.documents.preview_page(output_id, 0)
        self.assertEqual(self.documents._get(output_id)["last_used_at"], self.old)

    def test_print_quality_has_separate_cache_and_is_cleanable(self):
        if not (importlib.util.find_spec("pypdfium2") or importlib.util.find_spec("fitz")):
            self.skipTest("隔离环境缺少PDF图片组件")
        from PIL import Image
        output_id, folder, _ = self.output()
        preview = self.documents.preview_page(output_id, 1)
        print_page = self.documents.preview_page(output_id, 1, print_quality=True)
        self.assertNotEqual(preview, print_page)
        self.assertEqual(print_page.name, "print-page-1.png")
        with Image.open(preview) as normal, Image.open(print_page) as high:
            self.assertAlmostEqual(high.width / normal.width, 2.5 / 1.5, delta=0.02)
        record = self.documents._get(output_id)
        self.assertTrue({"preview_1", "print_preview_1"} <= record["files"].keys())
        self.assertNotEqual(record["last_used_at"], self.old)
        self.expire(output_id)
        self.assertEqual(self.documents.cleanup_expired(now=self.reference)["cleaned"], 1)
        self.assertFalse(folder.exists())

    def test_dry_run_does_not_change_files_or_metadata(self):
        output_id, folder, before = self.output()
        result = self.documents.cleanup_expired(now=self.reference, dry_run=True)
        self.assertEqual(result["would_clean"], 1)
        self.assertEqual(result["cleaned"], 0)
        self.assertEqual(result["bytes_freed"], 0)
        self.assertTrue(folder.exists())
        self.assertEqual(self.documents._get(output_id), before)

    def test_recent_generating_and_print_queue_outputs_are_kept(self):
        recent, recent_dir, _ = self.output(age=(self.reference - timedelta(days=6)).isoformat())
        generating, generating_dir, _ = self.output(status="generating")
        queued, queued_dir, _ = self.output()
        self.documents.protected_output_ids = lambda: {queued}
        result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        self.assertTrue(all(p.exists() for p in (recent_dir, generating_dir, queued_dir)))
        self.assertTrue(any(x["id"] == queued and "打印" in x["reason"] for x in result["skipped"]))

    def test_reentrant_hold_and_shared_cross_instance_lock_skip_active_reads(self):
        output_id, folder, _ = self.output()
        other = ProductionDocuments(self.store)
        with self.documents.hold(output_id), self.documents.hold(output_id):
            result = other.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        entered, release = threading.Event(), threading.Event()
        def read():
            with self.documents.hold(output_id):
                entered.set()
                self.assertTrue(release.wait(3))
                self.assertTrue((folder / "合成资料.xlsx").read_bytes())
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read)
            self.assertTrue(entered.wait(3))
            try:
                result = other.cleanup_expired(now=self.reference)
                self.assertEqual(result["cleaned"], 0)
                self.assertTrue(any("读取" in x["reason"] for x in result["skipped"]))
            finally:
                release.set()
            future.result(timeout=3)
        self.assertEqual(other.cleanup_expired(now=self.reference)["cleaned"], 1)

    def test_unknown_or_modified_file_prevents_whole_batch_cleanup(self):
        unknown, unknown_dir, _ = self.output()
        (unknown_dir / "人工补充资料.txt").write_text("保留", encoding="utf-8")
        changed, changed_dir, _ = self.output()
        (changed_dir / "合成资料.xlsx").write_bytes(b"manually-edited")
        result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        self.assertEqual(len(result["skipped"]), 2)
        self.assertTrue((unknown_dir / "合成资料.xlsx").exists())
        self.assertEqual((changed_dir / "合成资料.xlsx").read_bytes(), b"manually-edited")

    def test_traversal_or_missing_registration_never_deletes_sources(self):
        output_id, folder, record = self.output()
        record["files"]["xlsx"]["path"] = "../production_order_sources/原生产单.xlsx"
        self.documents._save(record)
        result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        self.assertTrue(folder.exists())
        self.assertEqual(self.source.read_bytes(), b"must-remain")

    def test_symlink_batch_is_skipped(self):
        output_id, folder, record = self.output()
        linked = folder / "previews"
        try:
            linked.symlink_to(self.source.parent, target_is_directory=True)
        except OSError:
            self.skipTest("本机未授予创建符号链接权限")
        result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        self.assertTrue(self.source.exists())

    def test_windows_permission_failure_is_partial_and_retryable(self):
        output_id, folder, _ = self.output()
        original_unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path.name == "合成资料.pdf":
                raise PermissionError("模拟Windows打印程序占用")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", new=locked):
            result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["errors"]), 1)
        self.assertTrue((folder / "合成资料.pdf").exists())
        self.assertEqual(self.documents._get(output_id)["status"], "cleanup_failed")
        retried = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(retried["cleaned"], 1)
        self.assertFalse(folder.exists())

    def test_file_changed_after_plan_is_checked_again_before_unlink(self):
        output_id, folder, _ = self.output()
        original_plan = self.documents._cleanup_file_plan
        def changed(record):
            result = original_plan(record)
            (folder / "合成资料.xlsx").write_bytes(b"changed-after-check")
            return result
        with patch.object(self.documents, "_cleanup_file_plan", side_effect=changed):
            result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual((folder / "合成资料.xlsx").read_bytes(), b"changed-after-check")

    def test_stale_writer_cannot_resurrect_cleaned_history(self):
        output_id, _, before = self.output()
        self.documents.cleanup_expired(now=self.reference)
        with self.assertRaises(ArtifactsExpired):
            self.documents._save(before)

    def test_legacy_history_uses_created_time_and_can_generate_a_new_batch(self):
        output_id, _, record = self.output()
        record.pop("last_used_at")
        with self.store.connect() as conn:
            conn.execute("UPDATE documents_generated SET data_json=? WHERE id=?", (json.dumps(record), output_id))
        self.assertEqual(self.documents.cleanup_expired(now=self.reference)["cleaned"], 1)
        source = {"id": "SYNTHETIC-SOURCE", "filename": "合成生产单.xlsx", "sha256": record["source_sha256"],
                  "fields": {}, "products": [{"name": "合成产品", "quantity": 1, "unit": "个", "piece_specifications": ["1米"]}],
                  "checks": [], "totals_by_unit": {"个": 1}}
        def pdf(source_path, target_path):
            from pypdf import PdfWriter
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=120)
            writer.write(str(target_path))
            return 1
        with patch.object(self.documents, "_intake", return_value={"latest": source, "documents": [source]}), patch("app.production_documents._convert_pdf", side_effect=pdf):
            generated = self.documents.generate("SYNTHETIC-ORDER", "labels", "liruiqi")
        self.assertEqual(generated["batch"], 2)
        self.assertNotEqual(generated["id"], output_id)
        self.assertEqual(generated["status"], "ready")
        self.assertTrue(self.documents.file(generated["id"], "xlsx")[0].is_file())
        self.assertEqual(self.documents._get(output_id)["status"], "cleaned")

    def test_exact_seven_day_boundary_and_future_timestamps(self):
        expired, _, _ = self.output(age=(self.reference - timedelta(days=7)).isoformat())
        future, future_folder, _ = self.output(age=(self.reference + timedelta(days=1)).isoformat())
        result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned_ids"], [expired])
        self.assertTrue(future_folder.exists())

    def test_parallel_preview_updates_are_serialized_across_instances(self):
        if not (importlib.util.find_spec("pypdfium2") or importlib.util.find_spec("fitz")):
            self.skipTest("隔离环境缺少PDF图片组件")
        output_id, _, _ = self.output(pages=2)
        other = ProductionDocuments(self.store)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.documents.preview_page, output_id, 1)
            second = executor.submit(other.preview_page, output_id, 2)
            self.assertTrue(first.result(timeout=5).is_file())
            self.assertTrue(second.result(timeout=5).is_file())
        self.assertTrue({"preview_1", "preview_2"} <= self.documents._get(output_id)["files"].keys())

    def test_maintenance_runs_immediately_repeats_and_stops(self):
        _, folder, _ = self.output()
        two_runs, calls = threading.Event(), []
        cleanup = self.documents.cleanup_expired
        def run():
            result = cleanup()
            calls.append(result)
            if len(calls) >= 2:
                two_runs.set()
            return result
        with patch.object(self.documents, "cleanup_expired", side_effect=run):
            self.assertTrue(self.documents.start_maintenance(interval_seconds=0.05))
            self.assertFalse(self.documents.start_maintenance(interval_seconds=0.05))
            self.assertTrue(two_runs.wait(3))
            self.assertTrue(self.documents.stop_maintenance())
        self.assertFalse(folder.exists())
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(self.documents.cleanup_receipt.is_file())

    def test_invalid_policy_and_unavailable_queue_protection_fail_closed(self):
        _, folder, _ = self.output()
        for days in (0, -1, True, "7"):
            with self.assertRaises(ValueError):
                self.documents.cleanup_expired(retention_days=days)
        self.documents.protected_output_ids = lambda: (_ for _ in ()).throw(RuntimeError("打印队列不可读"))
        result = self.documents.cleanup_expired(now=self.reference)
        self.assertEqual(result["cleaned"], 0)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(folder.exists())


if __name__ == "__main__":
    unittest.main()
