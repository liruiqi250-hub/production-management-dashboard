"""OAuth回调到钉钉端内启动的短期交接；全部凭据与身份均为隔离合成样本。"""
import hashlib
import hmac
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app.dingtalk_login import DingTalkLogin, HANDOFF_COOKIE, HANDOFF_SECONDS
from tests.test_dingtalk_login import DingTalkFixture, DingTalkHttpTests, PASSWORD


class HandoffTests(DingTalkFixture, unittest.TestCase):
    def test_session_handoff_is_single_use_and_preserves_expiry_and_current_role(self):
        login = self.bind()
        session = login["token"]
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET role='viewer' WHERE username='exampleuser'")
            before = [tuple(row) for row in conn.execute("SELECT * FROM sessions ORDER BY token_hash")]
        ticket = self.ding.issue_oauth_handoff("session", session)
        self.assertNotIn(session, ticket)
        self.assertEqual(self.ding.consume_oauth_handoff(ticket, session=session), "session")
        self.assertEqual(self.ding.consume_oauth_handoff(ticket, session=session), "")
        self.assertEqual(self.store.authenticate(session)["role"], "viewer")
        with self.store.connect() as conn:
            self.assertEqual(before, [tuple(row) for row in conn.execute("SELECT * FROM sessions ORDER BY token_hash")])

    def test_binding_handoff_preserves_explicit_original_account_binding(self):
        pending = self.callback()["pending"]
        ticket = self.ding.issue_oauth_handoff("binding", pending)
        self.assertEqual(self.ding.consume_oauth_handoff(ticket, pending=pending), "binding")
        self.assertTrue(self.ding.status(pending)["binding_pending"])
        self.assertFalse(self.ding.status(username="exampleuser")["bound"])
        login = self.store.login("exampleuser", PASSWORD)
        self.ding.bind_pending(pending, login["token"])
        self.assertTrue(self.ding.status(username="exampleuser")["bound"])

    def test_forged_malformed_cross_cookie_and_cross_kind_handoffs_are_rejected(self):
        session = self.bind()["token"]
        other_session = self.store.login("otheruser", PASSWORD)["token"]
        pending = self.callback("example-other-identity")["pending"]
        ticket = self.ding.issue_oauth_handoff("session", session)
        malformed = (None, 1, "", "v1.invalid", ticket + ".extra", "x" * 257,
                     ticket[:-1] + ("0" if ticket[-1] != "0" else "1"), ticket.replace(".session.", ".binding."))
        for value in malformed:
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(self.ding.consume_oauth_handoff(value, session=session, pending=pending), "")
        self.assertEqual(self.ding.consume_oauth_handoff(ticket, session=other_session), "")
        self.assertEqual(self.ding.consume_oauth_handoff(ticket, pending=session), "")
        binding = self.ding.issue_oauth_handoff("binding", pending)
        self.assertEqual(self.ding.consume_oauth_handoff(binding, session=pending), "")
        self.assertEqual(self.ding.consume_oauth_handoff(binding, pending=other_session), "")
        # 无效尝试不会把合法浏览器对应的交接误消费。
        self.assertEqual(self.ding.consume_oauth_handoff(ticket, session=session), "session")

    def test_expiry_future_process_key_and_domain_separation_are_enforced(self):
        session = self.bind()["token"]
        current = int(time.time())
        with patch("app.dingtalk_login.time.time", return_value=current):
            ticket = self.ding.issue_oauth_handoff("session", session)
        for moment in (current - 1, current + HANDOFF_SECONDS + 1):
            with patch("app.dingtalk_login.time.time", return_value=moment):
                self.assertEqual(self.ding.consume_oauth_handoff(ticket, session=session), "")
        self.assertEqual(DingTalkLogin(self.store).consume_oauth_handoff(ticket, session=session), "")
        payload = ticket.rsplit(".", 1)[0]
        unseparated = hmac.new(self.ding._handoff_key, payload.encode("ascii"), hashlib.sha256).hexdigest()
        self.assertEqual(self.ding.consume_oauth_handoff(payload + "." + unseparated, session=session), "")
        with patch("app.dingtalk_login.time.time", return_value=current + HANDOFF_SECONDS):
            self.assertEqual(self.ding.consume_oauth_handoff(ticket, session=session), "session")

    def test_current_session_and_pending_validity_are_rechecked(self):
        for change in ("DELETE FROM sessions", "UPDATE sessions SET expires='2000-01-01'", "UPDATE users SET active=0 WHERE username='exampleuser'"):
            with self.subTest(change=change):
                with self.store.connect() as conn:
                    conn.execute("UPDATE users SET active=1 WHERE username='exampleuser'")
                session = self.store.login("exampleuser", PASSWORD)["token"]
                ticket = self.ding.issue_oauth_handoff("session", session)
                with self.store.connect() as conn:
                    conn.execute(change)
                self.assertEqual(self.ding.consume_oauth_handoff(ticket, session=session), "")
        for change in ("DELETE FROM dingtalk_pending", "UPDATE dingtalk_pending SET expires='2000-01-01'", "UPDATE dingtalk_pending SET corp_id='other-enterprise'"):
            with self.subTest(change=change):
                pending = self.callback("example-pending-identity")["pending"]
                ticket = self.ding.issue_oauth_handoff("binding", pending)
                with self.store.connect() as conn:
                    conn.execute(change)
                self.assertEqual(self.ding.consume_oauth_handoff(ticket, pending=pending), "")

    def test_concurrent_consumers_cannot_reuse_the_same_handoff(self):
        session = self.bind()["token"]
        ticket = self.ding.issue_oauth_handoff("session", session)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.ding.consume_oauth_handoff(ticket, session=session), range(2)))
        self.assertEqual(sorted(results), ["", "session"])


class HandoffHttpTests(DingTalkHttpTests):
    def oauth_return(self, subject="example-union-subject"):
        status, headers, _ = self.request("GET", "/auth/dingtalk/start")
        self.assertEqual(status, 303)
        state = parse_qs(urlsplit(dict(headers)["Location"]).query)["state"][0]
        oauth = self.cookies(headers)["dino_ding_oauth"].value
        with patch.object(self.ding, "exchange", return_value=subject):
            status, headers, _ = self.request("GET", "/auth/dingtalk/callback?state=" + state + "&authCode=example-code",
                                              cookie="dino_ding_oauth=" + oauth)
        self.assertEqual(status, 303)
        jar = self.cookies(headers)
        cookie = "; ".join(name + "=" + morsel.value for name, morsel in jar.items() if morsel.value)
        self.assertEqual(jar[HANDOFF_COOKIE]["httponly"], True)
        self.assertEqual(jar[HANDOFF_COOKIE]["samesite"], "Strict")
        self.assertEqual(jar[HANDOFF_COOKIE]["max-age"], str(HANDOFF_SECONDS))
        return cookie, jar

    def test_bound_oauth_return_is_verified_once_without_native_exchange(self):
        self.bind()
        cookie, jar = self.oauth_return()
        session = jar["dino_session"].value
        with patch.object(self.ding, "workbench_start") as native:
            status, headers, body = self.request("GET", "/api/auth/dingtalk", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["oauth_handoff"], "session")
        self.assertEqual(self.cookies(headers)[HANDOFF_COOKIE]["max-age"], "0")
        native.assert_not_called()
        self.assertEqual(json.loads(self.request("GET", "/api/auth/dingtalk", cookie=cookie)[2])["oauth_handoff"], "")
        value = json.loads(self.request("GET", "/api/me", cookie="dino_session=" + session)[2])
        self.assertEqual(value["user"]["username"], "exampleuser")
        self.assertFalse(value["user"]["permissions"]["manage_all"])

    def test_first_binding_return_survives_status_then_explicit_password_bind(self):
        cookie, jar = self.oauth_return("example-first-binding")
        pending = jar["dino_ding_binding"].value
        with patch.object(self.ding, "workbench_start") as native:
            status, _, body = self.request("GET", "/api/auth/dingtalk", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["oauth_handoff"], "binding")
        self.assertTrue(json.loads(body)["binding_pending"])
        self.assertTrue(self.ding.status(pending)["binding_pending"])
        native.assert_not_called()
        status, _, _ = self.request("POST", "/api/login", {"username": "exampleuser", "password": PASSWORD, "bind_dingtalk": True}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(self.ding.status(username="exampleuser")["bound"])

    def test_old_query_and_an_existing_session_cannot_create_handoff(self):
        session = self.bind()["token"]
        for suffix in ("?dingtalk=ok", "?dingtalk=bind", "?oauth_handoff=session"):
            status, _, body = self.request("GET", "/api/auth/dingtalk" + suffix, cookie="dino_session=" + session)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["oauth_handoff"], "")
        cookie, jar = self.oauth_return()
        wrong = self.store.login("otheruser", PASSWORD)["token"]
        cookie = cookie.replace(jar["dino_session"].value, wrong)
        self.assertEqual(json.loads(self.request("GET", "/api/auth/dingtalk", cookie=cookie)[2])["oauth_handoff"], "")

    def test_start_and_failed_callback_clear_previous_handoff(self):
        for path in ("/auth/dingtalk/start", "/api/auth/dingtalk/workbench/start", "/auth/dingtalk/callback?dingtalk=ok"):
            with self.subTest(path=path):
                _, jar = self.oauth_return("example-start-cleanup")
                status, headers, _ = self.request("GET", path, cookie=HANDOFF_COOKIE + "=" + jar[HANDOFF_COOKIE].value)
                self.assertIn(status, (200, 303))
                self.assertEqual(self.cookies(headers)[HANDOFF_COOKIE]["max-age"], "0")

    def test_logout_clears_handoff_and_revokes_its_session(self):
        self.bind()
        cookie, jar = self.oauth_return()
        session = jar["dino_session"].value
        csrf = self.store.authenticate(session)["csrf"]
        status, headers, _ = self.request("POST", "/api/logout", {}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        self.assertEqual(self.cookies(headers)[HANDOFF_COOKIE]["max-age"], "0")
        self.assertEqual(json.loads(self.request("GET", "/api/auth/dingtalk", cookie=cookie)[2])["oauth_handoff"], "")

    def test_gateway_handoff_issue_and_consumption_cookies_are_secure(self):
        from app.remote_access import RemoteGateway
        from tests.test_remote_access import GatewayHttpTests, ORIGIN, SECRET
        folder = self.store.private / "remote_access"
        folder.mkdir()
        (folder / (self.store.path.stem + ".json")).write_text(json.dumps({
            "enabled": True, "public_origin": ORIGIN, "gateway_secret": SECRET}), "utf-8")
        self.server.gateway = RemoteGateway(self.store)
        self.server.dingtalk = self.ding = DingTalkLogin(self.store, public_origin=ORIGIN)
        self.request = lambda method, path, data=None, cookie="", csrf="": GatewayHttpTests.remote(self, method, path, data, cookie)
        cookie, jar = self.oauth_return("example-public-binding")
        self.assertTrue(jar[HANDOFF_COOKIE]["secure"])
        status, headers, body = self.request("GET", "/api/auth/dingtalk", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["oauth_handoff"], "binding")
        self.assertTrue(self.cookies(headers)[HANDOFF_COOKIE]["secure"])


if __name__ == "__main__":
    unittest.main()
