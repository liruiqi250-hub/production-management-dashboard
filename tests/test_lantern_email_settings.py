"""邮箱设置与授权隔离的合成验收。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.lantern_email_settings import save, settings, test_connection
from app.lantern_sources import load_sources

def payload(**overrides):
    return {"account": "synthetic@example.test", "host": "imap.example.test", "folders": ["INBOX"],
            "senders": [], "all_senders": True, "password": "Synthetic-Secret-Only", **overrides}

class EmailSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_save_private_read_never_returns_password(self):
        self.assertFalse(settings(self.root)["configured"])
        result = save(self.root, payload())
        self.assertTrue(result["password_saved"])
        self.assertNotIn("Synthetic-Secret", json.dumps(result))
        self.assertNotIn("password", result)
        self.assertNotIn("credential_file", result)
        self.assertNotIn("Synthetic-Secret", (self.root / "private/lantern_sources.json").read_text())
        self.assertTrue(load_sources(self.root)["email"]["all_senders"])

    def test_blank_password_only_for_same_identity(self):
        save(self.root, payload())
        save(self.root, payload(password="", folders=["INBOX", "Archive"]))
        secret_path = self.root / "private/lantern_email_secret.json"
        before = secret_path.read_bytes()
        self.assertEqual(json.loads(before)["password"], "Synthetic-Secret-Only")
        for change in ({"account": "other@example.test"}, {"host": "other.example.test"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                save(self.root, payload(password="", **change))
        self.assertEqual(secret_path.read_bytes(), before)

    def test_preserves_wechat_and_refuses_malformed_config(self):
        path = self.root / "private/lantern_sources.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema": 1, "wechat_groups": ["虚构授权彩灯群"]}))
        save(self.root, payload())
        self.assertEqual(load_sources(self.root)["wechat_groups"], ["虚构授权彩灯群"])
        path.write_text("broken")
        with self.assertRaises(ValueError):
            save(self.root, payload())
        self.assertEqual(path.read_text(), "broken")

    def test_explicit_scope_and_initial_password_required(self):
        for value in (payload(all_senders=False), payload(all_senders="yes"), payload(senders=["*@example.test"]), payload(password="")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                save(self.root, value)
        save(self.root, payload(all_senders=False, senders=["supplier@example.test"]))
        self.assertFalse(settings(self.root)["all_senders"])

    def test_connection_readonly_without_save_or_fetch(self):
        conn = MagicMock()
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [])
        with patch("app.lantern_email_settings.imaplib.IMAP4_SSL", return_value=conn) as connect:
            self.assertTrue(test_connection(self.root, payload())["ok"])
        conn.select.assert_called_once_with('"INBOX"', readonly=True)
        conn.uid.assert_not_called()
        conn.logout.assert_called_once()
        self.assertIsNotNone(connect.call_args.kwargs["ssl_context"])
        self.assertFalse((self.root / "private/lantern_sources.json").exists())

    def test_connection_failure_never_echoes_password(self):
        import imaplib
        conn = MagicMock()
        conn.login.side_effect = imaplib.IMAP4.error("Synthetic-Secret-Only server text")
        with patch("app.lantern_email_settings.imaplib.IMAP4_SSL", return_value=conn):
            with self.assertRaises(ValueError) as error:
                test_connection(self.root, payload())
        self.assertNotIn("Synthetic-Secret", str(error.exception))
        conn.logout.assert_called_once()
