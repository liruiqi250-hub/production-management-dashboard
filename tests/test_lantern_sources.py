"""彩灯来源隔离与范围核验；所有微信文件、邮件及密码均为临时合成数据。"""
from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.lantern_sources import _window as source_window, collect_sources, load_sources
from app.store import TZ


START = datetime(2026, 10, 7, tzinfo=TZ)
END = datetime(2026, 10, 14, tzinfo=TZ)
GROUP = "虚构彩灯授权群"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def email_config():
    return {"host": "imap.example.test", "port": 993,
            "account": "synthetic@example.test", "folders": ["INBOX"],
            "senders": ["supplier@example.test"],
            "credential_file": "private/lantern_email_secret.json"}


def synthetic_email(*, sender="supplier@example.test", body="虚构彩灯项目：请核对生产资料。",
                    date="Thu, 01 Jan 2026 00:00:00 +0000", attachment=None):
    message = EmailMessage(policy=SMTP)
    message["From"] = "虚构供应商 <" + sender + ">"
    message["To"] = "synthetic@example.test"
    message["Subject"] = "虚构彩灯生产资料"
    message["Date"] = date
    message.set_content(body)
    if attachment is not None:
        filename, content = attachment
        message.add_attachment(content, maintype="application", subtype="octet-stream", filename=filename)
    return message.as_bytes()


class LanternSourcesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="lantern-source-synthetic-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.folder = self.root / "private" / "lantern_jobs" / ("1" * 32)
        self.folder.mkdir(parents=True)
        self.config_path = self.root / "private" / "lantern_sources.json"
        self.window_patch = patch("app.lantern_sources._window", return_value=(START, END))
        self.window_patch.start()
        self.addCleanup(self.window_patch.stop)

    def config(self, **values):
        config = {"schema": 1, **values}
        write_json(self.config_path, config)
        return config

    def wechat_runtime(self):
        for path in (self.root / "vendor" / "wechat" / "python" / "python.exe",
                     self.root / "scripts" / "collect_scoped_wechat_V0.1.3.py"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic fixture only")

    def wechat_payload(self):
        raw = {"chat_name": GROUP, "message_count": 1,
               "messages": [{"source_db": "message\\synthetic.db", "local_id": 7,
                              "time": "2026-10-12 09:30:00", "sender": "虚构测试人员",
                              "content": "虚构彩灯项目资料待核对。", "transcript": "虚构语音转写，待核听。",
                              "media": []}]}
        return raw

    def emit_wechat(self, args, *, mutate=None, empty=False):
        target = Path(args[args.index("--out") + 1])
        source = target / "lantern_01" / "chat_full_parsed.json"
        raw = self.wechat_payload()
        row = {"source_id": "lantern_01", "group": GROUP, "index": 1,
               "binding_verified": True, "exact_match_count": 1,
               "status": "exported", "message_count": 1,
               "source_json": str(source)}
        if empty:
            row.update(status="verified_empty_range", message_count=0, query_audit={"sql_date_filter": True})
            row.pop("source_json")
        manifest = {"status": "exported", "sensitive_cache_cleanup": True,
                    "start_inclusive": START.isoformat(), "end_exclusive": END.isoformat(),
                    "sources": [{"source_id": "lantern_01", "group": GROUP,
                                 "binding_verified": True, "exact_match_count": 1}],
                    "export_results": [row]}
        if mutate:
            mutate(manifest, raw, row)
        if not empty:
            write_json(source, raw)
            row.setdefault("source_sha256", hashlib.sha256(source.read_bytes()).hexdigest())
        write_json(target / "collection_manifest_V0.1.3.json", manifest)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def collect_wechat(self, *, mutate=None, empty=False):
        self.wechat_runtime()
        config = self.config(wechat_groups=[GROUP])
        with patch("app.lantern_sources.subprocess.run",
                   side_effect=lambda args, **kwargs: self.emit_wechat(args, mutate=mutate, empty=empty)) as process:
            result = collect_sources(self.root, self.folder, config)
        return result, process

    def imap(self, raw=None, *, internaldate="12-Oct-2026 01:30:00 +0000", uids=b"17", size=None):
        raw = synthetic_email() if raw is None else raw
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.login.return_value = ("OK", [b"synthetic login"])
        connection.select.return_value = ("OK", [b"1"])
        connection.logout.return_value = ("BYE", [b"synthetic logout"])

        def uid(command, *args):
            if command.lower() == "search":
                return "OK", [uids]
            if command.lower() == "fetch":
                header = (f'17 (UID 17 RFC822.SIZE {len(raw) if size is None else size} '
                          f'INTERNALDATE "{internaldate}")').encode("ascii")
                if "BODY.PEEK[]" in args[-1]:
                    return "OK", [(header, raw), b")"]
                return "OK", [header]
            raise AssertionError("不应调用会修改邮箱的命令：" + command)

        connection.uid.side_effect = uid
        return connection

    def collect_email(self, connection, *, config=None):
        config = self.config(email=email_config()) if config is None else config
        write_json(self.root / "private" / "lantern_email_secret.json", {"password": "synthetic-password-only"})
        with patch("app.lantern_sources.imaplib.IMAP4_SSL", return_value=connection) as connect:
            result = collect_sources(self.root, self.folder, config)
        return result, connect

    def test_missing_config_does_not_inherit_dinosaur_binding(self):
        write_json(self.root / "private" / "wechat_binding.json", {"production_groups": ["不应继承的虚构恐龙群"]})
        with self.assertRaises(ValueError):
            load_sources(self.root)

    def test_load_explicit_groups_and_schema_version_alias(self):
        self.config(wechat_groups=[GROUP])
        self.assertEqual(load_sources(self.root)["wechat_groups"], [GROUP])
        write_json(self.config_path, {"schema_version": 1, "wechat_groups": [GROUP]})
        self.assertEqual(load_sources(self.root)["wechat_groups"], [GROUP])

    def test_empty_or_unknown_source_config_is_rejected(self):
        for value in ({"schema": 1}, {"schema": 1, "wechat_groups": []},
                      {"schema": 1, "wechat_groups": [GROUP], "command": "must-not-run"},
                      {"schema": 2, "wechat_groups": [GROUP]},
                      {"schema": True, "wechat_groups": [GROUP]},
                      {"schema": "1", "wechat_groups": [GROUP]}):
            with self.subTest(config=value):
                write_json(self.config_path, value)
                with self.assertRaises(ValueError):
                    load_sources(self.root)

    def test_groups_are_exact_nonempty_unique_and_bounded(self):
        for groups in ([""], ["   "], [GROUP, GROUP], ["虚构\n群"], [f"虚构群{i}" for i in range(51)]):
            with self.subTest(groups=groups):
                self.config(wechat_groups=groups)
                with self.assertRaises(ValueError):
                    load_sources(self.root)

    def test_explicit_email_configuration_is_accepted_without_reading_secret(self):
        self.config(email=email_config())
        loaded = load_sources(self.root)
        self.assertEqual(loaded["email"]["account"], "synthetic@example.test")
        self.assertFalse((self.root / "private" / "lantern_email_secret.json").exists())

    def test_email_configuration_rejects_widening_and_unsafe_fields(self):
        changes = ({"folders": []}, {"folders": ["INBOX\r\nEXPUNGE"]}, {"folders": ["收件箱"]},
                   {"senders": []}, {"senders": ["*@example.test"]}, {"account": "not-an-email"},
                   {"host": ""}, {"host": "imap.example.test\n"}, {"port": 143},
                   {"credential_file": "../private/lantern_email_secret.json"},
                   {"password": "must-not-be-stored-in-config"}, {"smtp_host": "must-not-send.example.test"})
        for change in changes:
            with self.subTest(change=change):
                configuration = email_config()
                configuration.update(change)
                self.config(email=configuration)
                with self.assertRaises(ValueError):
                    load_sources(self.root)

    def test_wechat_collects_only_lantern_scope_and_exact_date_window(self):
        result, process = self.collect_wechat()
        args = [str(arg) for arg in process.call_args.args[0]]
        self.assertEqual(Path(args[0]), self.root / "vendor" / "wechat" / "python" / "python.exe")
        for flag, value in (("--scope", "lantern"), ("--phase", "export"), ("--group-indices", "1"),
                            ("--start", "2026-10-07"), ("--end-exclusive", "2026-10-14")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--media", args)
        self.assertIn("--transcribe-voices", args)
        self.assertFalse(process.call_args.kwargs.get("shell", False))
        self.assertEqual(len(result["messages"]), 1)
        message = result["messages"][0]
        self.assertEqual(message["ref"], "wechat:lantern_01:message\\synthetic.db:7")
        self.assertEqual(message["source"], "微信:" + GROUP)
        self.assertIn("虚构语音转写", message["text"])
        self.assertTrue(result["limitations"])

    def test_wechat_verified_empty_range_is_valid_coverage(self):
        result, _ = self.collect_wechat(empty=True)
        self.assertEqual(result["messages"], [])
        self.assertIsInstance(result["limitations"], list)

    def test_wechat_accepts_collector_actual_cutoff_before_requested_midnight(self):
        cutoff = "2026-10-13T15:47:12+08:00"
        result, _ = self.collect_wechat(mutate=lambda manifest, raw, row: manifest.update(end_exclusive=cutoff))
        self.assertEqual(len(result["messages"]), 1)
        self.assertTrue(any(cutoff in limitation for limitation in result["limitations"]))

    def test_wechat_rejects_message_after_actual_collector_cutoff(self):
        def after_cutoff(manifest, raw, row):
            manifest["end_exclusive"] = "2026-10-13T15:47:12+08:00"
            raw["messages"][0]["time"] = "2026-10-13 16:00:00"

        with self.assertRaises(ValueError):
            self.collect_wechat(mutate=after_cutoff)

    def test_wechat_manifest_rejects_wrong_group_scope_and_failed_cleanup(self):
        mutations = (
            lambda manifest, raw, row: row.update(group="另一个未授权群"),
            lambda manifest, raw, row: row.update(source_id="production_01"),
            lambda manifest, raw, row: row.update(binding_verified=False),
            lambda manifest, raw, row: row.update(exact_match_count=2),
            lambda manifest, raw, row: manifest.update(status="partial"),
            lambda manifest, raw, row: manifest.update(sensitive_cache_cleanup=False),
            lambda manifest, raw, row: manifest.update(export_results=[]),
            lambda manifest, raw, row: manifest.update(start_inclusive="2026-10-01T00:00:00+08:00"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.collect_wechat(mutate=mutate)

    def test_wechat_rejects_source_hash_tamper(self):
        with self.assertRaises(ValueError):
            self.collect_wechat(mutate=lambda manifest, raw, row: row.update(source_sha256="0" * 64))

    def test_wechat_rejects_source_path_outside_job_folder(self):
        outside = self.root / "private" / "unrelated.json"
        write_json(outside, self.wechat_payload())

        def outside_source(manifest, raw, row):
            row.update(source_json=str(outside), source_sha256=hashlib.sha256(outside.read_bytes()).hexdigest())

        with self.assertRaises(ValueError):
            self.collect_wechat(mutate=outside_source)

    def test_wechat_rechecks_message_count_and_time_range(self):
        for mutate in (lambda manifest, raw, row: raw.update(message_count=2),
                       lambda manifest, raw, row: raw["messages"][0].update(time="2026-09-01 00:00:00")):
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.collect_wechat(mutate=mutate)

    def test_wechat_subprocess_failure_is_not_reported_as_empty_success(self):
        self.wechat_runtime()
        config = self.config(wechat_groups=[GROUP])
        with patch("app.lantern_sources.subprocess.run", return_value=SimpleNamespace(returncode=2)), \
                self.assertRaises(ValueError):
            collect_sources(self.root, self.folder, config)

    def test_email_is_readonly_and_uses_internaldate_instead_of_date_header(self):
        connection = self.imap()
        result, connect = self.collect_email(connection)
        self.assertEqual(connect.call_args.args, ("imap.example.test", 993))
        self.assertIn("ssl_context", connect.call_args.kwargs)
        connection.login.assert_called_once_with("synthetic@example.test", "synthetic-password-only")
        connection.select.assert_called_once_with('"INBOX"', readonly=True)
        searches = [call.args for call in connection.uid.call_args_list if call.args[0].lower() == "search"]
        self.assertEqual(len(searches), 1)
        search_text = " ".join(str(arg) for arg in searches[0])
        for token in ("SINCE", "06-Oct-2026", "BEFORE", "15-Oct-2026", "FROM", "supplier@example.test"):
            self.assertIn(token, search_text)
        body_fetches = [call.args for call in connection.uid.call_args_list if "BODY.PEEK[]" in str(call.args)]
        self.assertEqual(len(body_fetches), 1)
        self.assertEqual(len(result["messages"]), 1)
        message = result["messages"][0]
        folder_hash = hashlib.sha256(b"INBOX").hexdigest()[:12]
        self.assertEqual(message["ref"], f"email:{folder_hash}:17")
        self.assertEqual(message["source"], "邮箱:synthetic@example.test/INBOX")
        self.assertTrue(message["time"].startswith("2026-10-12"))
        self.assertIn("虚构彩灯项目", message["text"])
        for command in ("store", "append", "expunge", "sendmail"):
            getattr(connection, command).assert_not_called()

    def test_email_rechecks_sender_even_when_search_claims_a_match(self):
        connection = self.imap(synthetic_email(sender="unapproved@example.test"))
        with self.assertRaises(ValueError):
            self.collect_email(connection)

    def test_email_rechecks_internaldate_window_even_when_date_header_is_current(self):
        connection = self.imap(synthetic_email(date="Mon, 12 Oct 2026 09:30:00 +0800"),
                               internaldate="01-Oct-2026 01:30:00 +0000")
        result, _ = self.collect_email(connection)
        self.assertEqual(result["messages"], [])
        self.assertFalse(any("BODY.PEEK[]" in str(call.args) for call in connection.uid.call_args_list))

    def test_email_utc_previous_date_is_included_when_china_date_is_in_window(self):
        # IMAP SEARCH日期不换算时区；UTC的6日20点属于中国窗口7日凌晨4点。
        connection = self.imap(internaldate="06-Oct-2026 20:00:00 +0000")
        result, _ = self.collect_email(connection)
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["time"], "2026-10-07T04:00:00+08:00")
        searches = [call.args for call in connection.uid.call_args_list if call.args[0].lower() == "search"]
        self.assertIn("06-Oct-2026", str(searches))

    def test_email_search_uid_duplicates_are_deduplicated(self):
        connection = self.imap(uids=b"17 17")
        result, _ = self.collect_email(connection)
        self.assertEqual(len(result["messages"]), 1)
        body_fetches = [call.args for call in connection.uid.call_args_list if "BODY.PEEK[]" in str(call.args)]
        self.assertEqual(len(body_fetches), 1)

    def test_email_caps_message_count_before_fetching_bodies(self):
        connection = self.imap(uids=" ".join(str(number) for number in range(1, 1002)).encode("ascii"))
        with self.assertRaises(ValueError):
            self.collect_email(connection)
        self.assertFalse(any("BODY.PEEK[]" in str(call.args) for call in connection.uid.call_args_list))

    def test_email_caps_message_bytes_before_fetching_body(self):
        connection = self.imap(size=20 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            self.collect_email(connection)
        self.assertFalse(any("BODY.PEEK[]" in str(call.args) for call in connection.uid.call_args_list))

    def test_email_rejects_body_size_different_from_server_metadata(self):
        raw = synthetic_email()
        connection = self.imap(raw, size=len(raw) + 1)
        with self.assertRaises(ValueError):
            self.collect_email(connection)

    def test_email_attachment_is_saved_only_under_current_job(self):
        content = b"synthetic attachment contents"
        connection = self.imap(synthetic_email(attachment=("../../synthetic-report.pdf", content)))
        result, _ = self.collect_email(connection)
        attachments = list((self.folder / "email_attachments").rglob("*"))
        saved = [path for path in attachments if path.is_file()]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), content)
        self.assertIn(self.folder, saved[0].resolve().parents)
        self.assertTrue(result["limitations"])

    def test_attached_email_body_is_not_misattributed_to_authorized_outer_sender(self):
        outer = EmailMessage(policy=SMTP)
        outer["From"] = "supplier@example.test"
        outer["To"] = "synthetic@example.test"
        outer["Subject"] = "虚构附加邮件"
        outer.set_content("OUTER-SYNTHETIC-BODY")
        inner = EmailMessage(policy=SMTP)
        inner["From"] = "unapproved@example.test"
        inner["Subject"] = "不应解析的虚构邮件附件"
        inner.set_content("INNER-ATTACHMENT-MUST-NOT-BECOME-OUTER-BODY")
        outer.add_attachment(inner, filename="synthetic-forwarded.eml")
        connection = self.imap(outer.as_bytes())
        result, _ = self.collect_email(connection)
        self.assertEqual(len(result["messages"]), 1)
        self.assertIn("OUTER-SYNTHETIC-BODY", result["messages"][0]["text"])
        self.assertNotIn("INNER-ATTACHMENT-MUST-NOT-BECOME-OUTER-BODY", result["messages"][0]["text"])
        self.assertTrue(any("附件" in limitation for limitation in result["limitations"]))
        saved = [path for path in (self.folder / "email_attachments").rglob("*") if path.is_file()]
        self.assertEqual(len(saved), 1)
        self.assertIn(b"INNER-ATTACHMENT-MUST-NOT-BECOME-OUTER-BODY", saved[0].read_bytes())

    def test_email_long_body_is_truncated_with_explicit_limitation(self):
        connection = self.imap(synthetic_email(body="虚构长正文" * 5000))
        result, _ = self.collect_email(connection)
        self.assertLess(len(result["messages"][0]["text"]), 17000)
        self.assertTrue(any("截" in limitation for limitation in result["limitations"]))

    def test_oversized_email_attachment_is_not_saved_and_is_disclosed(self):
        content = b"x" * (10 * 1024 * 1024 + 1)
        connection = self.imap(synthetic_email(attachment=("synthetic-large.pdf", content)))
        result, _ = self.collect_email(connection)
        self.assertEqual(len(result["messages"]), 1)
        saved = [path for path in (self.folder / "email_attachments").rglob("*") if path.is_file()]
        self.assertEqual(saved, [])
        self.assertTrue(any("10MB" in limitation for limitation in result["limitations"]))

    def test_missing_email_password_never_opens_imap_connection(self):
        config = self.config(email=email_config())
        with patch("app.lantern_sources.imaplib.IMAP4_SSL") as connect, self.assertRaises(ValueError):
            collect_sources(self.root, self.folder, config)
        connect.assert_not_called()


class SourceWindowTests(unittest.TestCase):
    def test_default_window_covers_seven_china_calendar_days(self):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 13, 15, 47, 12, tzinfo=tz)

        with patch("app.lantern_sources.datetime", FixedDatetime):
            start, end = source_window()
        self.assertEqual(start, START)
        self.assertEqual(end, END)
        self.assertEqual((end - start).days, 7)
        self.assertEqual(start.tzinfo, TZ)


if __name__ == "__main__":
    unittest.main()
