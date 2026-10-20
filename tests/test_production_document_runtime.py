"""生产资料轮子锁与离线安装安全测试；只用小型合成档，不联网或构建正式包。"""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

from scripts import build_production_document_runtime as runtime


class ProductionDocumentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-production-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "stage"
        self.source = Path(self.temp.name) / "source"
        self.cache = Path(self.temp.name) / "cache"
        self.cache.mkdir()
        (self.source / "dependencies").mkdir(parents=True)
        self.python = self.root / "vendor/python"
        self.python.mkdir(parents=True)
        self.pth = self.python / "python313._pth"
        self.pth.write_text("python313.zip\n.\n../../\n", encoding="utf-8")
        original_lock = Path(__file__).resolve().parents[1] / "dependencies/production-documents.lock.json"
        self.lock = json.loads(original_lock.read_text("utf-8"))
        for item in self.lock["components"]:
            _, _, modules = runtime.COMPONENTS[item["id"]]
            members = {module + "/__init__.py": b"# synthetic module; never imported\n" for module in modules}
            for license_info in item["licenses"]:
                data = b"Synthetic redistribution license.\n"
                members[license_info["path"]] = data
                license_info["sha256"] = hashlib.sha256(data).hexdigest()
            item["license_sha256"] = next(info["sha256"] for info in item["licenses"] if info["path"] == item["license_path"])
            self.write_wheel(item, members)
        self.save_lock()

    def save_lock(self):
        (self.source / "dependencies/production-documents.lock.json").write_text(json.dumps(self.lock), encoding="utf-8")

    def write_wheel(self, item, members):
        archive = self.cache / item["archive_filename"]
        with zipfile.ZipFile(archive, "w") as package:
            for name, data in members.items():
                package.writestr(name, data)
        item["archive_bytes"] = archive.stat().st_size
        item["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()

    def mutate_last_wheel(self, name, data, *, symlink=False):
        item = self.lock["components"][-1]
        archive = self.cache / item["archive_filename"]
        with zipfile.ZipFile(archive) as package:
            members = {entry.filename: package.read(entry) for entry in package.infolist()}
        if symlink:
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as package:
                for path, value in members.items():
                    package.writestr(path, value)
                package.writestr(entry, data)
            item["archive_bytes"] = archive.stat().st_size
            item["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        else:
            members[name] = data
            self.write_wheel(item, members)
        self.save_lock()

    def build(self):
        return runtime.build_production_document_runtime(self.root, self.cache, self.source, offline=True)

    def test_locked_versions_platform_and_all_license_sources(self):
        lock = runtime.validate_lock(copy.deepcopy(self.lock))
        self.assertEqual({item["id"] for item in lock["components"]}, set(runtime.COMPONENTS))
        for item in lock["components"]:
            self.assertNotIn("cp313t", item["archive_filename"])
            self.assertTrue(item["url"].startswith("https://files.pythonhosted.org/packages/"))
            for license_info in item["licenses"]:
                self.assertEqual(license_info["source_url"], item["url"] + "#" + license_info["path"])

    def test_offline_install_is_repeatable_preserves_licenses_and_isolates_site(self):
        with patch.object(runtime.urllib.request, "build_opener") as network:
            result = self.build()
            repeated = self.build()
            network.assert_not_called()
        self.assertEqual(result, repeated)
        self.assertEqual(self.pth.read_text("utf-8").splitlines(), ["python313.zip", ".", "../../", "site-packages"])
        self.assertNotIn("import site", self.pth.read_text("utf-8"))
        for item in self.lock["components"]:
            for license_info in item["licenses"]:
                data = (self.python / "site-packages" / license_info["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), license_info["sha256"])
        self.assertTrue((self.python / "PRODUCTION_DOCUMENT_RUNTIME.json").is_file())

    def test_missing_or_tampered_cache_does_not_write_packages(self):
        last = self.lock["components"][-1]
        path = self.cache / last["archive_filename"]
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "缓存校验"):
            self.build()
        self.assertFalse((self.python / "site-packages").exists())
        path.unlink()
        with self.assertRaisesRegex(ValueError, "离线缓存"):
            self.build()

    def test_lock_rejects_unofficial_source_wrong_abi_duplicate_and_missing_components(self):
        changes = [
            lambda lock: lock["components"][0].update(url="https://untrusted.invalid/package.whl"),
            lambda lock: lock["components"][-1].update(archive_filename="pillow-13.0.0-cp313-cp313t-win_amd64.whl"),
            lambda lock: lock["components"][0].update(metadata_url="https://untrusted.invalid/metadata.json"),
            lambda lock: lock["components"][0].update(archive_bytes=True),
            lambda lock: lock["components"][0].update(licenses=[]),
            lambda lock: lock["components"][0].update(id=[]),
            lambda lock: lock["components"][0]["licenses"][0].update(path=None),
            lambda lock: lock["components"].pop(),
            lambda lock: lock["components"].__setitem__(1, copy.deepcopy(lock["components"][0])),
        ]
        for change in changes:
            invalid = copy.deepcopy(self.lock)
            change(invalid)
            with self.subTest(change=change), self.assertRaises(ValueError):
                runtime.validate_lock(invalid)

    def test_zip_traversal_is_rejected_before_any_component_is_written(self):
        self.mutate_last_wheel("../../escape.py", b"do not write")
        with self.assertRaisesRegex(ValueError, "路径校验"):
            self.build()
        self.assertFalse((self.python / "site-packages").exists())
        self.assertFalse((Path(self.temp.name) / "escape.py").exists())

    def test_zip_symlink_is_rejected_before_any_component_is_written(self):
        self.mutate_last_wheel("PIL/synthetic_link", b"../../escape", symlink=True)
        with self.assertRaisesRegex(ValueError, "链接"):
            self.build()
        self.assertFalse((self.python / "site-packages").exists())

    def test_pth_startup_and_unexpected_root_files_are_rejected(self):
        self.mutate_last_wheel("PIL/inject.pth", b"import synthetic_injection")
        with self.assertRaisesRegex(ValueError, "未授权路径或启动"):
            self.build()
        self.assertFalse((self.python / "site-packages").exists())

    def test_unexpected_root_startup_module_is_rejected(self):
        self.mutate_last_wheel("sitecustomize.py", b"raise RuntimeError('never run')")
        with self.assertRaisesRegex(ValueError, "未授权路径或启动"):
            self.build()
        self.assertFalse((self.python / "site-packages").exists())

    def test_changed_license_and_active_interpreter_startup_import_are_rejected(self):
        path = self.lock["components"][-1]["license_path"]
        self.mutate_last_wheel(path, b"modified license")
        with self.assertRaisesRegex(ValueError, "许可证校验"):
            self.build()
        self.assertFalse((self.python / "site-packages").exists())
        self.pth.write_text("python313.zip\n.\nimport site\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "启动导入"):
            self.build()

    def test_nonofficial_redirect_is_blocked_before_following(self):
        handler = runtime.OfficialRedirectOnly()
        request = urllib.request.Request(self.lock["components"][0]["url"])
        for url in ("http://files.pythonhosted.org/packages/a.whl", "https://untrusted.invalid/a.whl",
                    "https://files.pythonhosted.org.evil.invalid/packages/a.whl",
                    "https://user@files.pythonhosted.org/packages/a.whl"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "禁止重定向"):
                handler.redirect_request(request, None, 302, "redirect", {}, url)

    def test_download_checks_bytes_and_digest_before_cache_commit(self):
        item = self.lock["components"][0]
        path = self.cache / item["archive_filename"]
        payload = path.read_bytes()
        path.unlink()

        class Response(io.BytesIO):
            def geturl(self):
                return item["url"]

        with patch.object(runtime.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response(payload)
            self.assertEqual(runtime.obtain_archive(item, self.cache), path)
        path.unlink()
        bad = bytearray(payload)
        bad[-1] ^= 1
        with patch.object(runtime.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response(bad)
            with self.assertRaisesRegex(ValueError, "下载校验"):
                runtime.obtain_archive(item, self.cache)
        self.assertFalse(path.exists())
        self.assertFalse(path.with_name(path.name + ".part").exists())


if __name__ == "__main__":
    unittest.main()
