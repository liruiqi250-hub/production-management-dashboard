"""独立发布器的本地安全边界测试；不访问网络或真实业务材料。"""

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.publish_github import PublishError, collect_files, require_committed, sync_files, require_non_regressing_version


class PublishGitHubTests(unittest.TestCase):
    def test_older_workspace_cannot_replace_newer_remote(self):
        with self.assertRaisesRegex(PublishError, "须先合并远端功能"):
            require_non_regressing_version("0.2.14", "0.3.4")
        require_non_regressing_version("0.3.5", "0.3.4")
        require_non_regressing_version("0.3.10", "0.3.9")
        require_non_regressing_version("0.3.4", "0.3.4")
        with self.assertRaises(PublishError):
            require_non_regressing_version("0.2.14", "unknown")

    def test_only_tracked_whitelisted_text_is_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixtures = {"VERSION": b"0.2.5\n", "app/main.py": b"print('ok')\n",
                        "docs/guide.md": b"guide\n", "tests/frontend.test.cjs": b"'use strict';\n", "app/icon.png": b"image",
                        "app/cache.db": b"database", "outputs/report.md": b"local",
                        "app/runtime/state.json": b"{}", "web/untracked.js": b"local"}
            for name, data in fixtures.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            tracked = [name for name in fixtures if name != "web/untracked.js"]
            self.assertEqual(set(collect_files(root, tracked)), {"VERSION", "app/main.py", "docs/guide.md", "tests/frontend.test.cjs"})

    def test_sensitive_names_and_credentials_stop_before_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            # 敏感文件名无需访问其内容；不存在也必须拒绝。
            for name in ["private/data.txt", "app/.env", "docs/api-token.txt", "app/key.pem"]:
                with self.subTest(name=name), self.assertRaises(PublishError) as caught:
                    collect_files(root, [name])
                self.assertIn(name, str(caught.exception))
            path = root / "app" / "main.py"
            path.parent.mkdir()
            samples = ["gh" + "p_" + "a" * 36,
                       "-----BEGIN " + "PRIVATE KEY-----\nvalue",
                       "API_" + "TOKEN='" + "realcredential0123456789" + "'\n"]
            for sample in samples:
                path.write_text(sample, encoding="utf-8")
                with self.subTest(kind=sample[:4]), self.assertRaises(PublishError) as caught:
                    collect_files(root, ["app/main.py"])
                self.assertEqual(str(caught.exception), "文件疑似包含凭据或私钥：app/main.py")

    def test_sync_rejects_escaping_paths_before_mutating(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            target = parent / "publish"
            target.mkdir()
            outside = parent / "keep.txt"
            outside.write_bytes(b"keep")
            old = target / "old.txt"
            old.write_bytes(b"old")
            for unsafe in ["../keep.txt", "/keep.txt", "C:/keep.txt", ".git/config", ".git./config", "app/../../keep.txt"]:
                with self.subTest(path=unsafe), self.assertRaises(PublishError):
                    sync_files(target, {"app/main.py": b"new"}, ["old.txt", unsafe])
                self.assertEqual(outside.read_bytes(), b"keep")
                self.assertEqual(old.read_bytes(), b"old")
                self.assertFalse((target / "app").exists())
            changed = sync_files(target, {"app/main.py": b"new"}, ["old.txt"])
            self.assertEqual(set(changed), {"old.txt", "app/main.py"})
            self.assertFalse(old.exists())
            self.assertEqual((target / "app/main.py").read_bytes(), b"new")

    def test_tracked_edits_must_be_committed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
            git("init")
            git("config", "user.name", "Publisher Test")
            git("config", "user.email", "publisher-test@example.invalid")
            (root / "VERSION").write_text("0.2.5\n", encoding="utf-8")
            git("add", "VERSION")
            git("commit", "-m", "fixture")
            require_committed(root)
            (root / "VERSION").write_text("0.2.6\n", encoding="utf-8")
            with self.assertRaisesRegex(PublishError, "未提交"):
                require_committed(root)


if __name__ == "__main__":
    unittest.main()
