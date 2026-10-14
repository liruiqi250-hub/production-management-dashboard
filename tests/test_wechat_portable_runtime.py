"""公开微信运行档的离线校验、解压边界与 GPL 二进制排除。"""
import hashlib
import base64
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import bundle_wechat_runtime as runtime


class WeChatPortableRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, members):
        path = self.root / "fixture.whl"
        with zipfile.ZipFile(path, "w") as target:
            for name, data in members.items():
                target.writestr(name, data)
        return path

    def test_offline_requires_matching_size_and_hash(self):
        path = self.root / "locked.zip"
        path.write_bytes(b"public fixture")
        component = {"archive_filename": path.name, "archive_bytes": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.assertEqual(runtime.obtain_archive(component, self.root, offline=True), path)
        path.write_bytes(b"changed fixture")
        with self.assertRaisesRegex(runtime.WeChatBundleError, "校验失败"):
            runtime.obtain_archive(component, self.root, offline=True)
        path.unlink()
        with self.assertRaisesRegex(runtime.WeChatBundleError, "离线缓存缺少"):
            runtime.obtain_archive(component, self.root, offline=True)

    def test_imageio_gpl_binary_is_omitted_but_wrapper_and_license_remain(self):
        archive = self.archive({"imageio_ffmpeg/__init__.py": b"# BSD wrapper",
                                "imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe": b"MZ GPL binary",
                                "imageio_ffmpeg-0.6.0.dist-info/LICENSE": b"BSD license"})
        python = self.root / "python"
        runtime._extract_wheel(archive, python, {"id": "imageio-ffmpeg"})
        self.assertTrue((python / "Lib/site-packages/imageio_ffmpeg/__init__.py").is_file())
        self.assertTrue((python / "Lib/site-packages/imageio_ffmpeg-0.6.0.dist-info/LICENSE").is_file())
        self.assertFalse(list(python.rglob("*.exe")))

    def test_wheel_traversal_and_unapproved_pth_fail_before_any_file_is_written(self):
        for members in ({"valid.py": b"ok", "../../escape.py": b"bad"},
                        {"valid.py": b"ok", "startup.pth": b"import untrusted"}):
            with self.subTest(members=list(members)):
                archive = self.archive(members)
                python = self.root / "python"
                with self.assertRaises((runtime.WeChatBundleError, runtime.PublishError)):
                    runtime._extract_wheel(archive, python, {"id": "fixture"})
                self.assertFalse(python.exists())
                self.assertFalse((self.root.parent / "escape.py").exists())

    def test_record_refresh_tracks_installed_files_and_omits_removed_binary(self):
        archive = self.archive({"imageio_ffmpeg/__init__.py": b"wrapper",
                                "imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe": b"GPL binary",
                                "imageio_ffmpeg-0.6.0.dist-info/LICENSE": b"original BSD license",
                                "imageio_ffmpeg-0.6.0.dist-info/RECORD":
                                b"imageio_ffmpeg/__init__.py,old,7\n"
                                b"imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe,old,10\n"
                                b"imageio_ffmpeg-0.6.0.dist-info/LICENSE,old,20\n"
                                b"imageio_ffmpeg-0.6.0.dist-info/RECORD,,\n"})
        python = self.root / "vendor/wechat/python"
        runtime._extract_wheel(archive, python, {"id": "imageio-ffmpeg"})
        wrapper = python / "Lib/site-packages/imageio_ffmpeg/__init__.py"
        wrapper.write_bytes(b"changed file fixture")
        runtime.refresh_installation_records(self.root)
        record = python / "Lib/site-packages/imageio_ffmpeg-0.6.0.dist-info/RECORD"
        with record.open(encoding="utf-8", newline="") as stream:
            rows = {row[0]: row[1:] for row in csv.reader(stream)}
        digest = base64.urlsafe_b64encode(hashlib.sha256(wrapper.read_bytes()).digest()).rstrip(b"=").decode()
        self.assertEqual(rows["imageio_ffmpeg/__init__.py"], ["sha256=" + digest, str(wrapper.stat().st_size)])
        self.assertNotIn("imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe", rows)
        self.assertEqual((record.parent / "LICENSE").read_bytes(), b"original BSD license")
        # media 构建后的二次刷新必须仍然可重复。
        runtime.refresh_installation_records(self.root)

    def test_runtime_refuses_existing_target_to_preserve_binding_and_data(self):
        (self.root / "vendor/wechat").mkdir(parents=True)
        proof = self.root / "vendor/wechat/existing.txt"
        proof.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(runtime.WeChatBundleError, "拒绝覆盖"):
            runtime.build_wechat_runtime(self.root, self.root / "cache", offline=True)
        self.assertEqual(proof.read_text(encoding="utf-8"), "keep")

    def test_lock_has_complete_runtime_and_refuses_untrusted_url(self):
        path = Path(runtime.__file__).resolve().parents[1] / "dependencies/wechat-portable.lock.json"
        lock = runtime.validate_lock(path.read_bytes())
        self.assertEqual(len(lock["components"]), 24)
        lock["components"][0]["url"] = "https://example.com/python.tar.gz"
        with self.assertRaisesRegex(runtime.WeChatBundleError, "公开 HTTPS"):
            runtime.validate_lock(json.dumps(lock).encode())


if __name__ == "__main__":
    unittest.main()
