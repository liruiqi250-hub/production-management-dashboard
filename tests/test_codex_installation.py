"""接收者本机绑定与机器诊断边界，不访问生产绑定或微信内容。"""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import configure_wechat_binding as binding
from scripts import doctor_installation as doctor


class CodexInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codex_installation_")
        self.root = Path(self.temporary.name)
        (self.root / "VERSION").write_text("0.2.7\n", encoding="utf-8")
        self.db_dir = self.root / "synthetic_wechat"
        (self.db_dir / "authorized_test_account" / "db_storage").mkdir(parents=True)
        self.payload = {"db_dir": str(self.db_dir), "account": "authorized_test_account", "self_username": "wxid_authorized_test"}

    def tearDown(self):
        self.temporary.cleanup()

    def test_new_binding_no_overwrite_and_explicit_replace(self):
        self.assertEqual(binding.check_binding(self.root)["status"], "needs_configuration")
        result = binding.save_binding(self.payload, root=self.root)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(binding.check_binding(self.root)["status"], "ready")
        with self.assertRaisesRegex(binding.BindingError, "未覆盖"):
            binding.save_binding(self.payload, root=self.root)
        self.payload["self_username"] = "wxid_new_authorized_test"
        binding.save_binding(self.payload, root=self.root, replace=True)
        saved = json.loads((self.root / "private/wechat_binding.json").read_text())
        self.assertEqual(saved["self_username"], "wxid_new_authorized_test")
        self.assertFalse(list((self.root / "private").glob("*.tmp")))

    def test_binding_rejects_traversal_network_and_missing_structure(self):
        for account in ("../outside", "..", "C:other", "test/account", "test\\account"):
            with self.subTest(account=account), self.assertRaises(binding.BindingError):
                binding.save_binding({**self.payload, "account": account}, root=self.root)
        for directory in (r"\\server\share", "//server/share", "relative/directory"):
            with self.subTest(directory=directory), self.assertRaises(binding.BindingError):
                binding.save_binding({**self.payload, "db_dir": directory}, root=self.root)
        with self.assertRaisesRegex(binding.BindingError, "db_storage"):
            binding.save_binding({**self.payload, "account": "missing_account"}, root=self.root)
        self.assertFalse((self.root / "private").exists())

    def test_private_directory_reparse_point_is_rejected_before_write(self):
        target = self.root / "private"
        target.mkdir()
        real_lstat = Path.lstat
        class ReparseInfo:
            st_mode = 0o40755
            st_file_attributes = 0x400
        def lstat(path):
            return ReparseInfo() if path == target else real_lstat(path)
        with patch.object(Path, "lstat", lstat), self.assertRaisesRegex(binding.BindingError, "重解析"):
            binding.save_binding(self.payload, root=self.root)
        self.assertFalse((target / "wechat_binding.json").exists())

    def test_binding_check_output_never_echoes_private_values(self):
        binding.save_binding(self.payload, root=self.root)
        for result in (binding.check_binding(self.root), {"status": "error"}):
            output = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(str(self.db_dir), output)
            self.assertNotIn(self.payload["account"], output)
            self.assertNotIn(self.payload["self_username"], output)
        (self.root / "private/wechat_binding.json").write_text(json.dumps({**self.payload, "token": "synthetic_private_token"}))
        self.assertEqual(binding.check_binding(self.root)["status"], "error")

    def test_core_package_optional_wechat_and_full_package_requires_it(self):
        (self.root / "dependencies").mkdir()
        (self.root / "dependencies/wechat-collector.lock.json").write_text("{}")
        optional = doctor.check_wechat(self.root, required=False)
        required = doctor.check_wechat(self.root, required=True)
        self.assertEqual(optional[0]["status"], "optional")
        self.assertEqual(required[0]["status"], "error")

    def media_fixture(self):
        layouts = {"ffmpeg": "vendor/wechat/ffmpeg", "libsndfile": "vendor/wechat/python/Lib/site-packages/_soundfile_data"}
        components = []
        for ident, relative in layouts.items():
            folder = self.root / relative
            folder.mkdir(parents=True)
            payload = (ident + " synthetic permitted redistributable data").encode()
            (folder / "COPYING").write_bytes(payload)
            components.append({"id": ident, "files": {"COPYING": hashlib.sha256(payload).hexdigest()}})
        (self.root / "dependencies").mkdir(exist_ok=True)
        lock = {"schema": 1, "platform": "windows-x64", "components": components}
        (self.root / "dependencies/media-runtime.lock.json").write_text(json.dumps(lock))
        return lock

    def test_media_tampering_fails_fixed_file_hash(self):
        self.media_fixture()
        self.assertTrue(all(item["status"] == "ready" for item in doctor.check_media(self.root)))
        (self.root / "vendor/wechat/ffmpeg/COPYING").write_text("modified license")
        self.assertEqual(doctor.check_media(self.root)[0]["status"], "error")

    def test_media_lock_cannot_read_outside_package(self):
        lock = self.media_fixture()
        lock["components"][0]["files"] = {"../../../../outside": "a" * 64}
        (self.root / "dependencies/media-runtime.lock.json").write_text(json.dumps(lock))
        checks = doctor.check_media(self.root)
        self.assertEqual(checks[0]["id"], "media_integrity")
        self.assertEqual(checks[0]["status"], "error")

    def test_service_verifies_workspace_pid_and_listener_without_stopping(self):
        (self.root / "private").mkdir()
        (self.root / "private/server_V0.2.0.pid").write_text("12345")
        workspace_id = hashlib.sha256(str(self.root.resolve()).casefold().encode()).hexdigest()
        health = {"status": "ready", "version": "0.2.7", "workspace_id": workspace_id}
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self, url, timeout):
                self_url = url
                assert self_url == "http://127.0.0.1:8769/health"
                return Response(json.dumps(health).encode())
        with patch.object(doctor.urllib.request, "build_opener", return_value=Opener()), patch.object(doctor, "run_probe", return_value={"process_match": True, "listener_match": True}):
            self.assertEqual(doctor.check_service(self.root, "0.2.7", 8769)["status"], "ready")
            health["workspace_id"] = "another_workspace"
            result = doctor.check_service(self.root, "0.2.7", 8769)
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["workspace_id_matches"])
        self.assertEqual((self.root / "private/server_V0.2.0.pid").read_text(), "12345")

    def test_needs_configuration_returns_two_and_does_not_claim_collection(self):
        good = [{"id": "fixture", "status": "ready", "detail": "fixture"}]
        service = {"id": "dashboard_service", "status": "needs_configuration", "detail": "not started"}
        with patch.object(doctor, "check_core", return_value=good), patch.object(doctor, "check_wechat", return_value=good), patch.object(doctor, "check_service", return_value=service):
            result = doctor.diagnose(self.root, require_wechat=True)
        self.assertEqual(result["status"], "needs_configuration")
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["wechat_connected"])
        self.assertFalse(result["messages_read"])
        self.assertFalse(result["scheduled_task_installed"])
        self.assertNotIn(str(self.root), json.dumps(result))


if __name__ == "__main__":
    unittest.main()
