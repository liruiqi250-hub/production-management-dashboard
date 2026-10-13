"""便携包供应链与数据边界测试，使用可控的小型依赖档，绝不访问生产数据库。"""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_portable as bundle


def fixture_component(folder, ident, *, members=None):
    target, prefix, license_name, executable = bundle.COMPONENT_LAYOUT[ident]
    version = "3.13.16" if ident == "python" else "155.0.8059.39"
    license_data = b"Fixture dependency license; redistribution permitted.\n"
    filename = f"{ident}-{version}.zip"
    path = folder / filename
    contents = {prefix + executable: b"MZfixture", prefix + license_name: license_data}
    if ident == "python":
        contents["python313._pth"] = b"python313.zip\n.\n#import site\n"
    if members is not None:
        contents = members
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in contents.items():
            archive.writestr(name, value)
    return {"id": ident, "version": version, "target": target, "strip_prefix": prefix,
            "url": (f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip" if ident == "python" else
                    f"https://storage.googleapis.com/chrome-for-testing-public/{version}/win64/chrome-headless-shell-win64.zip"),
            "archive_filename": filename, "archive_sha256": bundle.sha256_file(path),
            "archive_bytes": path.stat().st_size,
            "license": {"path": license_name, "sha256": hashlib.sha256(license_data).hexdigest()}}


class PortableBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache = self.root / "runtime/dependency_cache"
        self.cache.mkdir(parents=True)
        self.python = fixture_component(self.cache, "python")
        self.chromium = fixture_component(self.cache, "chromium")
        self.lock = {"schema": 1, "platform": "windows-x64", "components": [self.python, self.chromium]}

    def tearDown(self):
        self.temporary.cleanup()

    def test_lock_rejects_untrusted_url_and_missing_component(self):
        invalid = copy.deepcopy(self.lock)
        invalid["components"][0]["url"] = "https://example.com/python.zip"
        with self.assertRaisesRegex(bundle.BundleError, "官方地址"):
            bundle.validate_lock(json.dumps(invalid).encode())
        invalid = copy.deepcopy(self.lock)
        invalid["components"].pop()
        with self.assertRaisesRegex(bundle.BundleError, "同时包含"):
            bundle.validate_lock(json.dumps(invalid).encode())

    def test_cached_binary_is_rehashed_even_in_offline_mode(self):
        archive = bundle.obtain_archive(self.python, self.cache, offline=True)
        data = bytearray(archive.read_bytes())
        data[-1] ^= 1
        archive.write_bytes(data)
        with self.assertRaisesRegex(bundle.BundleError, "缓存校验失败"):
            bundle.obtain_archive(self.python, self.cache, offline=True)

    def test_zip_path_traversal_rejected_before_any_extraction(self):
        self.python = fixture_component(self.cache, "python", members={
            "python.exe": b"MZ", "LICENSE.txt": b"Fixture dependency license; redistribution permitted.\n",
            "../../outside.txt": b"must not write"})
        with self.assertRaises(bundle.PublishError):
            bundle.extract_component(self.cache / self.python["archive_filename"], self.python, self.root / "stage")
        self.assertFalse((self.root / "stage/vendor/python/python.exe").exists())
        self.assertFalse((self.root / "outside.txt").exists())

    def test_missing_or_modified_license_prevents_distribution(self):
        self.python = fixture_component(self.cache, "python", members={"python.exe": b"MZ"})
        with self.assertRaisesRegex(bundle.BundleError, "许可证"):
            bundle.extract_component(self.cache / self.python["archive_filename"], self.python, self.root / "stage")
        self.python = fixture_component(self.cache, "python", members={"python.exe": b"MZ", "LICENSE.txt": b"different"})
        with self.assertRaisesRegex(bundle.BundleError, "许可证校验"):
            bundle.extract_component(self.cache / self.python["archive_filename"], self.python, self.root / "stage")

    def test_package_excludes_business_files_and_manifest_matches_all_payloads(self):
        files = {
            "VERSION": b"0.2.6\n", "app/__init__.py": b'__version__ = "0.2.6"\n',
            "web/index.html": b"<title>Fixture</title>",
            "启动生产看板_V0.2.6.cmd": b"@echo off\n",
            "dependencies/windows-x64.lock.json": json.dumps(self.lock).encode(),
            "private/production.sqlite3": b"business contents",
            "analysis/raw_chat.json": b"business contents",
            "outputs/report.txt": b"business contents",
        }
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        with patch.object(bundle, "_tracked", return_value=[name for name in files if not name.startswith("private/")]):
            result = bundle.build_bundle(self.root, offline=True)
        archive = Path(result["archive"])
        self.assertEqual(result["sha256"], bundle.sha256_file(archive))
        prefix = "production-dashboard-V0.2.6-windows-x64/"
        with zipfile.ZipFile(archive) as source:
            names = source.namelist()
            self.assertTrue(all(name.startswith(prefix) for name in names))
            self.assertFalse(any("/private/" in name or "/analysis/" in name or "/outputs/" in name or "/.git/" in name for name in names))
            manifest = json.loads(source.read(prefix + "SHA256SUMS.json"))
            self.assertEqual(set(names), {prefix + "SHA256SUMS.json"} | {prefix + name for name in manifest["files"]})
            for name, expected in manifest["files"].items():
                data = source.read(prefix + name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected["sha256"])
                self.assertEqual(len(data), expected["bytes"])
            self.assertEqual(source.read(prefix + "vendor/python/python313._pth"), b"python313.zip\n.\n../../\n")
            self.assertNotIn(b"import site", source.read(prefix + "vendor/python/python313._pth"))
            self.assertIn(prefix + "vendor/chromium/LICENSE.headless_shell", names)

    def test_accidentally_tracked_private_data_blocks_build(self):
        with patch.object(bundle, "_tracked", return_value=["private/production.sqlite3"]):
            with self.assertRaisesRegex(bundle.PublishError, "敏感文件"):
                bundle.build_bundle(self.root, offline=True)

    def test_successful_download_still_requires_expected_checksum(self):
        expected = copy.deepcopy(self.python)
        expected["archive_filename"] = "downloaded-python.zip"

        class Response(io.BytesIO):
            def geturl(self):
                return expected["url"]

        payload = (self.cache / self.python["archive_filename"]).read_bytes()
        with patch.object(bundle.urllib.request, "urlopen", return_value=Response(payload)):
            path = bundle.obtain_archive(expected, self.cache)
        self.assertEqual(bundle.sha256_file(path), expected["archive_sha256"])
        expected["archive_filename"] = "tampered-python.zip"
        wrong = bytearray(payload)
        wrong[-1] ^= 1
        with patch.object(bundle.urllib.request, "urlopen", return_value=Response(wrong)):
            with self.assertRaisesRegex(bundle.BundleError, "下载校验失败"):
                bundle.obtain_archive(expected, self.cache)
        self.assertFalse((self.cache / "tampered-python.zip").exists())
        self.assertFalse((self.cache / "tampered-python.zip.part").exists())


if __name__ == "__main__":
    unittest.main()
