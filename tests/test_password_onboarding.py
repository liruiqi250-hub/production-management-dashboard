"""仅使用临时库和虚构凭据，验证统一初始密码及首次登录改密隔离。"""

import http.client
import json
import secrets
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from app.server import DashboardServer
from app.store import Store, password_hash


class PasswordStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dino-password-onboarding-")
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.password = "Synthetic-Current-123"
        # 模拟已经完成首次改密的既有用户；绝不读取真实账号文件或业务库。
        with self.store.connect() as connection:
            for row in connection.execute("SELECT username,salt FROM users").fetchall():
                connection.execute(
                    "UPDATE users SET password_hash=?,must_change_password=0 WHERE username=?",
                    (password_hash(self.password, row["salt"]), row["username"]),
                )

    def make_ready_user(self, username, role="editor"):
        created = self.store.create_user(username, "虚构测试人员", role)
        self.store.change_password(username, created["initial_password"], self.password)
        return username

    def test_fresh_bootstrap_accounts_require_first_password_change(self):
        with tempfile.TemporaryDirectory(prefix="dino-new-password-schema-") as directory:
            fresh = Store(directory)
            users = fresh.users()
            self.assertEqual({u["username"] for u in users}, {"liruiqi", "zengyue"})
            for user in users:
                self.assertIs(user["must_change_password"], True)
                with fresh.connect() as connection:
                    raw = connection.execute("SELECT * FROM users WHERE username=?", (user["username"],)).fetchone()
                    self.assertIs(fresh.safe_user(raw)["must_change_password"], True)

    def test_old_six_column_users_migrate_without_forcing_existing_users(self):
        with tempfile.TemporaryDirectory(prefix="dino-old-password-schema-") as directory:
            private = Path(directory) / "private"
            private.mkdir()
            old_path = private / "production_V0.2.0.sqlite3"
            salt = secrets.token_hex(16)
            with closing(sqlite3.connect(old_path)) as connection, connection:
                connection.execute("""CREATE TABLE users(
                    username TEXT PRIMARY KEY,display_name TEXT NOT NULL,role TEXT NOT NULL,
                    salt TEXT NOT NULL,password_hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1)""")
                connection.execute("INSERT INTO users VALUES(?,?,?,?,?,1)",
                                   ("legacy_user", "虚构旧用户", "viewer", salt, password_hash(self.password, salt)))
            migrated = Store(directory)
            users = migrated.users()
            self.assertEqual(len(users), 1)
            self.assertIs(users[0]["must_change_password"], False)
            result = migrated.login("legacy_user", self.password)
            self.assertIs(result["user"]["must_change_password"], False)
            # 第二次启动不能再次迁移或更改账号状态。
            self.assertIs(Store(directory).users()[0]["must_change_password"], False)

    def test_created_and_individually_reset_users_require_change(self):
        created = self.store.create_user("new_person", "虚构新人员", "editor")
        initial = created["initial_password"]
        login = self.store.login("new_person", initial)
        self.assertIs(login["user"]["must_change_password"], True)
        with self.assertRaises(ValueError):
            self.store.create_api_token("new_person", "不得提前创建", 7)
        self.store.change_password("new_person", initial, self.password)
        self.assertIs(self.store.login("new_person", self.password)["user"]["must_change_password"], False)
        ready_session = self.store.login("new_person", self.password)
        ready_token = self.store.create_api_token("new_person", "虚构旧令牌", 7)
        reset = self.store.reset_user_password("new_person")
        self.assertIsNone(self.store.authenticate(ready_session["token"]))
        self.assertIsNone(self.store.authenticate(ready_token["token"], api=True))
        self.assertIsNone(self.store.login("new_person", self.password))
        self.assertIs(self.store.login("new_person", reset["initial_password"])["user"]["must_change_password"], True)
        self.assertIs(next(u for u in self.store.users() if u["username"] == "new_person")["must_change_password"], True)

    def test_bulk_reset_preserves_roles_and_disabled_state_revokes_all_access(self):
        self.make_ready_user("test_editor")
        self.make_ready_user("test_disabled", "viewer")
        old_sessions, old_tokens = [], []
        for user in self.store.users():
            username = user["username"]
            old_sessions.append(self.store.login(username, self.password)["token"])
            old_tokens.append(self.store.create_api_token(username, "虚构重置前令牌", 7)["token"])
        self.store.disable_user("test_disabled")
        before = {u["username"]: (u["role"], u["active"]) for u in self.store.users()}
        with self.store.connect() as connection:
            previous_audit_id = connection.execute("SELECT coalesce(max(id),0) FROM audit").fetchone()[0]
        temporary_password = "Mock2468"  # 虚构8位临时密码；本人新密码仍需10至120位。
        reset_names = self.store.reset_all_passwords(temporary_password, actor="liruiqi")
        self.assertEqual(set(reset_names), set(before))
        after = self.store.users()
        self.assertEqual({u["username"]: (u["role"], u["active"]) for u in after}, before)
        self.assertTrue(all(u["must_change_password"] is True for u in after))
        for token in old_sessions:
            self.assertIsNone(self.store.authenticate(token))
        for token in old_tokens:
            self.assertIsNone(self.store.authenticate(token, api=True))
        with self.store.connect() as connection:
            users = connection.execute("SELECT salt,password_hash FROM users").fetchall()
            self.assertEqual(len({u["salt"] for u in users}), len(users))
            self.assertEqual(len({u["password_hash"] for u in users}), len(users))
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM api_tokens WHERE revoked=0").fetchone()[0], 0)
            for user in users:
                self.assertEqual(user["password_hash"], password_hash(temporary_password, user["salt"]))
                self.assertNotEqual(user["password_hash"], temporary_password)
            audit = [dict(row) for row in connection.execute("SELECT * FROM audit WHERE id>?", (previous_audit_id,))]
        self.assertTrue(audit, "批量重置需留下可追溯审计")
        self.assertTrue(any(row["username"] == "liruiqi" for row in audit))
        serialized_audit = json.dumps(audit, ensure_ascii=False)
        for secret in (temporary_password, self.password, *old_sessions, *old_tokens):
            self.assertNotIn(secret, serialized_audit)
        for user in after:
            self.assertIsNone(self.store.login(user["username"], self.password))
            result = self.store.login(user["username"], temporary_password)
            if user["active"]:
                self.assertIs(result["user"]["must_change_password"], True)
            else:
                self.assertIsNone(result, "统一密码不能重新启用已停用的账号")

    def test_change_requires_different_valid_password_and_clears_flag(self):
        self.store.reset_all_passwords(self.password)
        session = self.store.login("liruiqi", self.password)
        for old, new in ((self.password, self.password), (self.password, "Short1234"),
                         (self.password, "X" * 121), ("Wrong-Original-123", "Synthetic-New-456")):
            with self.subTest(new_length=len(new), wrong_original=old != self.password):
                with self.assertRaises(ValueError):
                    self.store.change_password("liruiqi", old, new)
                self.assertIs(self.store.authenticate(session["token"])["must_change_password"], True)
        new_password = "Synthetic-New-456"
        self.store.change_password("liruiqi", self.password, new_password)
        self.assertIsNone(self.store.authenticate(session["token"]))
        self.assertIsNone(self.store.login("liruiqi", self.password))
        login = self.store.login("liruiqi", new_password)
        self.assertIs(login["user"]["must_change_password"], False)
        self.assertIs(next(u for u in self.store.users() if u["username"] == "liruiqi")["must_change_password"], False)
        token = self.store.create_api_token("liruiqi", "改密后允许只读", 7)
        self.assertIsNotNone(self.store.authenticate(token["token"], api=True))

    def test_existing_token_is_denied_if_owner_becomes_pending_change(self):
        session = self.store.login("liruiqi", self.password)
        token = self.store.create_api_token("liruiqi", "虚构有效令牌", 7)
        with self.store.connect() as connection:
            connection.execute("UPDATE users SET must_change_password=1 WHERE username='liruiqi'")
        # 即使令牌尚未被撤销，也必须检查账号当前待改密状态。
        self.assertIsNone(self.store.authenticate(token["token"], api=True))
        self.assertIs(self.store.authenticate(session["token"])["must_change_password"], True)
        with self.assertRaises(ValueError):
            self.store.create_api_token("liruiqi", "不得绕过待改密", 7)


class PasswordHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dino-password-http-")
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.password = "Synthetic-Onboard-123"
        self.store.reset_all_passwords(self.password)
        self.server = DashboardServer(("127.0.0.1", 0), self.store, "192.168.10.237")
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, data=None, headers=None):
        headers = dict(headers or {})
        if data is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(data)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, data, headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def login(self, username="liruiqi", password=None):
        status, body, headers = self.request("POST", "/api/login",
                                             {"username": username, "password": password or self.password})
        self.assertEqual(status, 200)
        result = json.loads(body)
        return result, {"Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-CSRF-Token": result["csrf"]}

    def assert_pending_denied(self, method, path, data, session):
        status, body, _ = self.request(method, path, data, session)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body).get("code"), "password_change_required")

    def test_pending_session_identifies_user_but_cannot_read_business(self):
        login, session = self.login()
        self.assertIs(login["user"]["must_change_password"], True)
        status, body, _ = self.request("GET", "/api/me", headers=session)
        self.assertEqual(status, 200)
        me = json.loads(body)
        self.assertIs(me["user"]["must_change_password"], True)
        self.assertEqual(me["csrf"], session["X-CSRF-Token"])
        for path in ("/api/dashboard", "/api/v1/overview", "/api/v1/items", "/api/users", "/api/tokens",
                     "/api/reports?kind=daily&end=2026-10-12&format=json",
                     "/api/v1/reports?kind=weekly&end=2026-10-12&format=csv"):
            with self.subTest(path=path):
                self.assert_pending_denied("GET", path, None, session)

    def test_pending_session_cannot_mutate_items_users_or_tokens(self):
        _, session = self.login()
        for method, path, data in (
            ("POST", "/api/items", {"title": "不得新增的虚构事项"}),
            ("PATCH", "/api/items/nonexistent", {"status": "处理中"}),
            ("POST", "/api/users", {"username": "blocked_user", "display_name": "不得新增", "role": "viewer"}),
            ("PATCH", "/api/users/zengyue", {"active": False}),
            ("POST", "/api/users/zengyue/reset-password", {"reset": True}),
            ("POST", "/api/tokens", {"label": "不得新增", "days": 7}),
            ("PATCH", "/api/tokens/nonexistent", {"revoked": True}),
        ):
            with self.subTest(path=path):
                self.assert_pending_denied(method, path, data, session)
        self.assertNotIn("blocked_user", {u["username"] for u in self.store.users()})
        self.assertTrue(next(u for u in self.store.users() if u["username"] == "zengyue")["active"])

    def test_pending_password_and_logout_still_require_csrf(self):
        _, session = self.login()
        unsafe_headers = {"Cookie": session["Cookie"]}
        self.assertEqual(self.request("POST", "/api/password",
                                      {"old_password": self.password, "new_password": "Synthetic-New-789"}, unsafe_headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/logout", {}, unsafe_headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/me", headers=session)[0], 200)
        self.assertEqual(self.request("POST", "/api/logout", {}, session)[0], 200)
        self.assertEqual(self.request("GET", "/api/me", headers=session)[0], 401)

    def test_successful_change_requires_relogin_and_unlocks_only_that_account(self):
        _, session = self.login()
        for old, new in ((self.password, self.password), (self.password, "TooShort"),
                         ("Wrong-Original", "Synthetic-New-789")):
            self.assertEqual(self.request("POST", "/api/password",
                                          {"old_password": old, "new_password": new}, session)[0], 400)
        self.assert_pending_denied("GET", "/api/v1/items", None, session)
        new_password = "Synthetic-New-789"
        self.assertEqual(self.request("POST", "/api/password",
                                      {"old_password": self.password, "new_password": new_password}, session)[0], 200)
        self.assertEqual(self.request("GET", "/api/me", headers=session)[0], 401)
        changed, new_session = self.login(password=new_password)
        self.assertIs(changed["user"]["must_change_password"], False)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=new_session)[0], 200)
        self.assertEqual(self.request("POST", "/api/tokens", {"label": "改密后只读", "days": 7}, new_session)[0], 201)
        other, other_session = self.login("zengyue")
        self.assertIs(other["user"]["must_change_password"], True)
        self.assert_pending_denied("GET", "/api/v1/items", None, other_session)


if __name__ == "__main__":
    unittest.main()
