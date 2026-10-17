"""钉钉协议、浏览器绑定与原账号权限边界；只用隔离数据及合成身份。"""
import http.client
import io
from http.cookies import SimpleCookie
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app.dingtalk_login import DingTalkLogin, LoginError, SCOPE
from app.server import DashboardServer
from app.store import Store, digest

PASSWORD = "example-password-1234"


class DingTalkFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dingtalk_test_")
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(self.temporary.name)
        for name in ("exampleuser", "otheruser"):
            created = self.store.create_user(name, "合成员工", "editor")
            self.store.change_password(name, created["initial_password"], PASSWORD)
        self.config = {"enabled": True, "client_id": "dingexampleclient", "client_secret": "example-client-secret-value",
                       "corp_id": "dingexamplecorp", "redirect_uri": "http://127.0.0.1:8769/auth/dingtalk/callback"}
        self.config_path = self.store.private / "dingtalk" / (self.store.path.stem + ".json")
        self.config_path.parent.mkdir()
        self.save_config()
        self.ding = DingTalkLogin(self.store)

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config), "utf-8")

    def callback(self, subject="example-union-subject"):
        url, cookie = self.ding.start()
        state = parse_qs(urlsplit(url).query)["state"][0]
        with patch.object(self.ding, "exchange", return_value=subject):
            return self.ding.callback(state, cookie, "example-auth-code")

    def bind(self, username="exampleuser", subject="example-union-subject"):
        pending = self.callback(subject)["pending"]
        login = self.store.login(username, PASSWORD)
        self.ding.bind_pending(pending, login["token"])
        return login

class DingTalkTests(DingTalkFixture, unittest.TestCase):
    def test_masked_or_whitespace_secret_is_rejected(self):
        for secret in ("example****masked-secret", "example-secret\nvalue", " example-secret-value"):
            self.config["client_secret"] = secret
            self.save_config()
            self.assertFalse(DingTalkLogin(self.store).enabled)

    def test_failure_diagnostic_never_writes_raw_response_or_credentials(self):
        body = json.dumps({"code": "invalidParameter.idOrSecret.notFound", "message": "secret-example-do-not-store"}).encode()
        error = urllib.error.HTTPError("https://api.dingtalk.com/example", 400, "bad", {}, io.BytesIO(body))
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(LoginError) as caught:
                self.ding.exchange("raw-auth-code-do-not-store")
        self.ding.record_failure(caught.exception)
        saved = self.config_path.with_name(self.store.path.stem + ".last_failure.json").read_text("utf-8")
        self.assertIn("token_http_400_invalidParameter.idOrSecret.notFound", saved)
        for forbidden in ("secret-example-do-not-store", "raw-auth-code-do-not-store", self.config["client_secret"]):
            self.assertNotIn(forbidden, saved)

    def test_optional_config_and_malformed_config_do_not_break_password_login(self):
        self.config_path.unlink()
        self.assertFalse(DingTalkLogin(self.store).enabled)
        self.config_path.write_text("invalid json", "utf-8")
        self.assertFalse(DingTalkLogin(self.store).enabled)
        self.assertIsNotNone(self.store.login("exampleuser", PASSWORD))

    def test_config_rejects_external_callback_or_userinfo(self):
        synthetic_userinfo = urlsplit(self.config["redirect_uri"])._replace(netloc="example:example@127.0.0.1:8769").geturl()
        for url in ("https://example.com/auth/dingtalk/callback", synthetic_userinfo,
                    "http://127.0.0.1:8769/other", "http://127.0.0.1:8769/auth/dingtalk/callback?next=bad"):
            with self.subTest(url=url):
                self.config["redirect_uri"] = url
                self.save_config()
                self.assertFalse(DingTalkLogin(self.store).enabled)

    def test_start_uses_fixed_scope_and_does_not_store_raw_state(self):
        url, cookie = self.ding.start()
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(urlsplit(url).netloc, "login.dingtalk.com")
        self.assertEqual(query["scope"], [SCOPE])
        self.assertNotIn("mobile", SCOPE)
        self.assertNotIn("Calendar", SCOPE)
        self.assertEqual(query["corpId"], [self.config["corp_id"]])
        with self.store.connect() as conn:
            row = dict(conn.execute("SELECT * FROM dingtalk_states").fetchone())
        self.assertEqual(row["state_hash"], digest(query["state"][0]))
        self.assertEqual(row["cookie_hash"], digest(cookie))

    def test_wrong_browser_or_expired_state_never_exchanges_code(self):
        url, cookie = self.ding.start()
        state = parse_qs(urlsplit(url).query)["state"][0]
        with patch.object(self.ding, "exchange") as exchange:
            with self.assertRaises(LoginError):
                self.ding.callback(state, "other-browser", "example-code")
            with self.store.connect() as conn:
                conn.execute("UPDATE dingtalk_states SET expires='2000-01-01'")
            with self.assertRaises(LoginError):
                self.ding.callback(state, cookie, "example-code")
            exchange.assert_not_called()

    def test_state_is_consumed_before_exchange_and_cannot_replay(self):
        url, cookie = self.ding.start()
        state = parse_qs(urlsplit(url).query)["state"][0]
        with patch.object(self.ding, "exchange", side_effect=LoginError("合成失败")) as exchange:
            for _ in range(2):
                with self.assertRaises(LoginError):
                    self.ding.callback(state, cookie, "example-code")
            self.assertEqual(exchange.call_count, 1)

    def test_unknown_identity_requires_password_binding_no_auto_account(self):
        result = self.callback()
        self.assertIn("pending", result)
        self.assertNotIn("session", result)
        self.assertTrue(self.ding.status(result["pending"])["binding_pending"])
        self.assertEqual(len(self.store.users()), 4)
        with self.assertRaises(LoginError):
            self.ding.bind_pending(result["pending"], "forged-session")
        self.assertFalse(self.ding.status(username="exampleuser")["bound"])

    def test_bound_login_keeps_current_role_and_office_scope(self):
        self.bind()
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET role='viewer' WHERE username='exampleuser'")
        result = self.callback()["session"]
        self.assertEqual(result["user"]["username"], "exampleuser")
        self.assertEqual(result["user"]["role"], "viewer")
        self.assertEqual(self.store.authenticate(result["token"])["role"], "viewer")

    def test_disabled_or_password_change_required_accounts_are_not_bypassed(self):
        self.bind()
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET active=0 WHERE username='exampleuser'")
        with self.assertRaises(LoginError):
            self.callback()
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET active=1,must_change_password=1 WHERE username='exampleuser'")
        self.assertTrue(self.callback()["session"]["user"]["must_change_password"])

    def test_subject_or_account_conflict_preserves_original_binding(self):
        self.bind()
        with self.assertRaises(LoginError):
            self.bind("exampleuser", "another-subject")
        # 同一钉钉已经绑定时直接登录原账号，不会进入重新绑定步骤。
        self.assertEqual(self.callback()["session"]["user"]["username"], "exampleuser")
        with self.store.connect() as conn:
            rows = conn.execute("SELECT username FROM dingtalk_identities").fetchall()
        self.assertEqual([row[0] for row in rows], ["exampleuser"])

    def test_pending_is_single_use_and_unbind_revokes_sessions(self):
        pending = self.callback()["pending"]
        login = self.store.login("exampleuser", PASSWORD)
        self.ding.bind_pending(pending, login["token"])
        with self.assertRaises(LoginError):
            self.ding.bind_pending(pending, login["token"])
        sso = self.callback()["session"]
        self.ding.unbind("exampleuser")
        self.assertIsNone(self.store.authenticate(login["token"]))
        self.assertIsNone(self.store.authenticate(sso["token"]))
        self.assertIn("pending", self.callback())

    def test_exchange_checks_enterprise_and_discards_non_identity_fields(self):
        with patch.object(self.ding, "_api", return_value={"corpId": "othercorp", "accessToken": "example-token"}) as api:
            with self.assertRaises(LoginError):
                self.ding.exchange("example-code")
            self.assertEqual(api.call_count, 1)
        with patch.object(self.ding, "_api", side_effect=[{"corpId": self.config["corp_id"], "accessToken": "example-token"},
                         {"unionId": "example-subject", "nick": "管理员", "mobile": "example-mobile"}]) as api:
            self.assertEqual(self.ding.exchange("example-code"), "example-subject")
            self.assertEqual(api.call_args.args[0], "https://api.dingtalk.com/v1.0/contact/users/me")


class DingTalkHttpTests(DingTalkFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = DashboardServer(("127.0.0.1", 0), self.store, "127.0.0.1")
        self.config["redirect_uri"] = f"http://127.0.0.1:{self.server.server_port}/auth/dingtalk/callback"
        self.save_config()
        self.server.dingtalk = self.ding = DingTalkLogin(self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, data=None, cookie="", csrf=""):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf}
        c.request(method, path, json.dumps(data).encode() if data is not None else None, headers)
        r = c.getresponse()
        result = (r.status, r.getheaders(), r.read())
        c.close()
        return result

    @staticmethod
    def cookies(headers):
        jar = SimpleCookie()
        for key, value in headers:
            if key.lower() == "set-cookie":
                jar.load(value)
        return jar

    def test_http_first_bind_then_sso_and_session_permissions(self):
        status, headers, _ = self.request("GET", "/auth/dingtalk/start")
        self.assertEqual(status, 303)
        location = dict(headers)["Location"]
        state = parse_qs(urlsplit(location).query)["state"][0]
        cookie = self.cookies(headers)["dino_ding_oauth"].value
        self.assertIn("SameSite=Lax", str(headers))
        with patch.object(self.ding, "exchange", return_value="example-subject"):
            status, headers, _ = self.request("GET", f"/auth/dingtalk/callback?state={state}&authCode=example-code",
                                              cookie="dino_ding_oauth=" + cookie)
        pending = self.cookies(headers)["dino_ding_binding"].value
        self.assertEqual(dict(headers)["Location"], "/?dingtalk=bind")
        status, _, _ = self.request("POST", "/api/login", {"username": "exampleuser", "password": "wrong", "bind_dingtalk": True},
                                    cookie="dino_ding_binding=" + pending)
        self.assertEqual(status, 401)
        self.assertFalse(self.ding.status(username="exampleuser")["bound"])
        status, headers, body = self.request("POST", "/api/login", {"username": "exampleuser", "password": PASSWORD, "bind_dingtalk": True},
                                             cookie="dino_ding_binding=" + pending)
        self.assertEqual(status, 200, body)
        self.assertTrue(self.ding.status(username="exampleuser")["bound"])
        session = self.cookies(headers)["dino_session"].value
        status, _, body = self.request("GET", "/api/me", cookie="dino_session=" + session)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"]["role"], "editor")
        self.assertFalse(json.loads(body)["user"]["permissions"]["manage_all"])

    def test_http_unbind_requires_real_session_csrf_and_cannot_target_others(self):
        login = self.bind()
        cookies = "dino_session=" + login["token"]
        self.assertEqual(self.request("POST", "/api/auth/dingtalk/unbind", {}, cookie=cookies)[0], 403)
        self.assertTrue(self.ding.status(username="exampleuser")["bound"])
        self.assertEqual(self.request("POST", "/api/auth/dingtalk/unbind", {"username": "otheruser"},
                                      cookie=cookies, csrf=login["csrf"])[0], 200)
        self.assertFalse(self.ding.status(username="exampleuser")["bound"])

    def test_http_malformed_and_replayed_callbacks_do_not_issue_session(self):
        for query in ("", "?state=x&state=y&authCode=z", "?" + "&".join("x=1" for _ in range(20))):
            status, headers, _ = self.request("GET", "/auth/dingtalk/callback" + query)
            self.assertEqual(status, 303)
            self.assertEqual(dict(headers)["Location"], "/?dingtalk=failed")
            self.assertNotIn("dino_session", self.cookies(headers))


if __name__ == "__main__":
    unittest.main()
