"""核对发布 ZIP 本身，防止目录泄露、未声明文件或内容损坏。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.audit_portable import AuditError, audit_archive


class PortableAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def package(self, *, extra=None, tamper=False):
        files = {"VERSION": b"0.2.7\n", "vendor/python/python.exe": b"MZ public fixture"}
        manifest = {"schema": 1, "platform": "windows-x64", "version": "0.2.7", "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in files.items()}}
        if tamper:
            files["VERSION"] = b"0.2.8\n"
        path = self.root / "portable.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items():
                archive.writestr("portable/" + name, data)
            if extra:
                archive.writestr("portable/" + extra, b"unexpected")
            archive.writestr("portable/SHA256SUMS.json", json.dumps(manifest))
        return path

    def test_valid_package_hashes_every_file(self):
        result = audit_archive(self.package(), expected_version="0.2.7")
        self.assertTrue(result["all_file_hashes_verified"])
        self.assertEqual(result["files"], 3)

    def test_corrupted_file_is_rejected_even_when_size_matches(self):
        with self.assertRaisesRegex(AuditError, "SHA-256 不匹配"):
            audit_archive(self.package(tamper=True))

    def test_private_material_and_unlisted_file_are_rejected(self):
        for path, expected in (("private/wechat_binding.json", "禁止发布"),
                               ("unexpected.txt", "未列入清单")):
            with self.subTest(path=path), self.assertRaisesRegex(AuditError, expected):
                audit_archive(self.package(extra=path))


if __name__ == "__main__":
    unittest.main()
