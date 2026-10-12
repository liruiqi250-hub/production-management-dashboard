"""临时数据库及隔离端口的权限、持久化回归；不读取真实账号或生产库。"""

import copy
import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.normalize import MEDIA_FIELDS, SOURCE_GROUPS
from app.reports import build_report
from app.server import DashboardServer
from app.store import Store, TZ, digest, password_hash


def synthetic_snapshot():
    start, end = "2026-10-06T00:00:00+08:00", "2026-10-13T00:00:00+08:00"
    timestamp = "2026-10-07T10:00:00+08:00"
    groups = []
    for source_id, name in SOURCE_GROUPS.items():
        groups.append({"source_id": source_id, "group": name,
                       "message_count": 1 if source_id == "production_01" else 0,
                       "media_stats": {key: 0 for key in MEDIA_FIELDS},
                       "status": "exported" if source_id == "production_01" else "verified_empty_range",
                       "cutoff": end})
    return {
        "version": "0.2.0", "generated_at": "2026-10-12T15:30:00+08:00",
        "period": {"start": start, "end": end, "cutoff": end, "timezone": "Asia/Shanghai"},
        "coverage": {"groups": groups, "message_count": 1,
                     "media": {key: 0 for key in MEDIA_FIELDS},
                     "limitations": ["全部为虚构测试材料，与实际群业务无关。"]},
        "items": [{"id": "SYNTHETIC-001", "title": "虚构测试事项", "category": "任务",
                   "status": "待处理", "owner": None, "deadline": None,
                   "first_seen": timestamp, "last_seen": timestamp,
                   "summary": "虚构测试摘要", "next_step": "虚构下一步",
                   "source_id": "production_01", "group": SOURCE_GROUPS["production_01"],
                   "evidence": [{"locator": "production_01|message\\message_1.db:1",
                                 "time": timestamp, "sender": "虚构测试人员", "text": "虚构原文"}],
                   "closure_scope": "尚未闭环", "review_state": "人工精选待副厂复核", "closed_at": None}],
    }


class StoreServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dino-permission-tests-")
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.snapshot = synthetic_snapshot()
        self.store.import_snapshot(self.snapshot)
        self.password = "Synthetic-Pass-123"
        self.store.create_user("test_editor", "虚构处理人员", "editor")
        with self.store.connect() as connection:
            for username in ("liruiqi", "zengyue", "test_editor"):
                salt = connection.execute("SELECT salt FROM users WHERE username=?", (username,)).fetchone()[0]
                connection.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE username=?",
                                   (password_hash(self.password, salt), username))
        self.server = DashboardServer(("127.0.0.1", 0), self.store, "192.168.10.237")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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

    def login(self, username="liruiqi"):
        status, body, headers = self.request("POST", "/api/login", {"username": username, "password": self.password})
        self.assertEqual(status, 200)
        result = json.loads(body)
        return {"Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-CSRF-Token": result["csrf"]}

    def token(self, headers):
        status, body, _ = self.request("POST", "/api/tokens", {"label": "虚构测试令牌", "days": 7}, headers)
        self.assertEqual(status, 201)
        return json.loads(body)

    def test_anonymous_data_and_reports_denied(self):
        for path in ("/api/v1/items", "/api/dashboard", "/api/users",
                     "/api/v1/reports?kind=daily&end=2026-10-12&format=json"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 401)

    def test_viewer_cannot_modify_production_or_create_users(self):
        viewer = self.login("zengyue")
        for method, path, data in (
            ("PATCH", "/api/items/SYNTHETIC-001", {"owner": "不应写入"}),
            ("POST", "/api/items", {"title": "不应新增"}),
            ("POST", "/api/users", {"username": "bad_user", "display_name": "不应新增", "role": "viewer"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, data, viewer)[0], 403)

    def test_csrf_origin_and_host_enforced(self):
        admin = self.login()
        for headers in ({"Cookie": admin["Cookie"]},
                        {**admin, "Origin": "https://untrusted.test"},
                        {**admin, "Host": "untrusted.test"}):
            self.assertEqual(self.request("PATCH", "/api/items/SYNTHETIC-001", {"owner": "不应写入"}, headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/login", {"username": "zengyue", "password": self.password},
                                      {"Origin": "https://untrusted.test"})[0], 403)

    def test_personal_token_readonly_even_for_admin(self):
        admin = self.login()
        token = self.token(admin)
        bearer = {"Authorization": "Bearer " + token["token"]}
        for path in ("/api/v1/overview", "/api/v1/items",
                     "/api/v1/reports?kind=daily&end=2026-10-12&format=json",
                     "/api/v1/reports?kind=weekly&end=2026-10-12&format=html",
                     "/api/v1/reports?kind=daily&end=2026-10-12&format=csv"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, headers=bearer)[0], 200)
        for method, path, data in (
            ("PATCH", "/api/items/SYNTHETIC-001", {"owner": "不应写入"}),
            ("POST", "/api/items", {"title": "不应新增"}),
            ("POST", "/api/tokens", {"label": "不应新增", "days": 7}),
            ("POST", "/api/users", {"username": "bad_user", "display_name": "不应新增"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, data, bearer)[0], 403)

    def test_token_only_hash_stored_and_secret_not_relisted(self):
        admin = self.login()
        token = self.token(admin)
        with self.store.connect() as connection:
            row = connection.execute("SELECT token_hash FROM api_tokens WHERE id=?", (token["id"],)).fetchone()
            self.assertEqual(row[0], digest(token["token"]))
        status, body, _ = self.request("GET", "/api/tokens", headers=admin)
        self.assertEqual(status, 200)
        self.assertNotIn(token["token"], body.decode())
        self.assertNotIn("token_hash", body.decode())

    def test_revoked_expired_disabled_and_password_change_invalidate(self):
        editor = self.login("test_editor")
        revoked = self.token(editor)
        self.assertEqual(self.request("PATCH", "/api/tokens/" + revoked["id"], {"revoked": True}, editor)[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/items", headers={"Authorization": "Bearer " + revoked["token"]})[0], 401)
        expired = self.token(editor)
        with self.store.connect() as connection:
            connection.execute("UPDATE api_tokens SET expires=? WHERE id=?",
                               ((datetime.now(TZ) - timedelta(seconds=1)).isoformat(), expired["id"]))
        self.assertEqual(self.request("GET", "/api/v1/items", headers={"Authorization": "Bearer " + expired["token"]})[0], 401)
        old_token = self.token(editor)
        self.assertEqual(self.request("POST", "/api/password", {"old_password": self.password, "new_password": "Synthetic-New-Pass-456"}, editor)[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=editor)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/items", headers={"Authorization": "Bearer " + old_token["token"]})[0], 401)
        admin = self.login()
        self.assertEqual(self.request("PATCH", "/api/users/zengyue", {"active": False}, admin)[0], 200)
        self.assertIsNone(self.store.login("zengyue", self.password))

    def test_disabled_user_session_and_tokens_immediately_denied(self):
        editor = self.login("test_editor")
        token = self.token(editor)
        self.store.disable_user("test_editor")
        self.assertEqual(self.request("GET", "/api/v1/items", headers=editor)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/items", headers={"Authorization": "Bearer " + token["token"]})[0], 401)

    def test_admin_enable_disable_and_role_changes_invalidate_old_access(self):
        admin = self.login()
        editor = self.login("test_editor")
        original_token = self.token(editor)
        original_bearer = {"Authorization": "Bearer " + original_token["token"]}
        for actor in (editor, self.login("zengyue"), original_bearer):
            self.assertEqual(self.request("PATCH", "/api/users/test_editor", {"role": "viewer"}, actor)[0], 403)
        self.assertEqual(self.request("PATCH", "/api/users/test_editor", {"active": False}, admin)[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=editor)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=original_bearer)[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"username": "test_editor", "password": self.password})[0], 401)
        self.assertEqual(self.request("PATCH", "/api/users/test_editor", {"active": True}, admin)[0], 200)
        # 重新启用不能复活原登录或原令牌。
        self.assertEqual(self.request("GET", "/api/v1/items", headers=editor)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=original_bearer)[0], 401)
        editor = self.login("test_editor")
        self.assertEqual(self.request("PATCH", "/api/items/SYNTHETIC-001", {"status": "处理中"}, editor)[0], 200)
        editor_token = self.token(editor)
        self.assertEqual(self.request("PATCH", "/api/users/test_editor", {"role": "viewer"}, admin)[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=editor)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/items", headers={"Authorization": "Bearer " + editor_token["token"]})[0], 401)
        viewer = self.login("test_editor")
        self.assertEqual(self.request("GET", "/api/v1/items", headers=viewer)[0], 200)
        self.assertEqual(self.request("PATCH", "/api/items/SYNTHETIC-001", {"status": "待处理"}, viewer)[0], 403)
        self.assertEqual(self.request("PATCH", "/api/users/test_editor", {"role": "editor"}, admin)[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=viewer)[0], 401)
        self.assertEqual(self.request("PATCH", "/api/items/SYNTHETIC-001", {"status": "待处理"}, self.login("test_editor"))[0], 200)
        for changes in ({"role": "admin"}, {"active": "false"}):
            self.assertEqual(self.request("PATCH", "/api/users/test_editor", changes, admin)[0], 400)
        self.assertEqual(self.request("PATCH", "/api/users/liruiqi", {"active": False}, admin)[0], 400)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=admin)[0], 200)

    def test_admin_password_reset_revokes_old_credentials_and_protects_self(self):
        admin = self.login()
        editor = self.login("test_editor")
        token = self.token(editor)
        bearer = {"Authorization": "Bearer " + token["token"]}
        reset_path = "/api/users/test_editor/reset-password"
        for actor in (editor, self.login("zengyue"), bearer):
            self.assertEqual(self.request("POST", reset_path, {"reset": True}, actor)[0], 403)
        status, body, _ = self.request("POST", reset_path, {"reset": True}, admin)
        self.assertEqual(status, 200)
        reset_result = json.loads(body)
        self.assertEqual(reset_result["username"], "test_editor")
        new_password = reset_result["initial_password"]
        self.assertNotEqual(new_password, self.password)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=editor)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=bearer)[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"username": "test_editor", "password": self.password})[0], 401)
        status, _, _ = self.request("POST", "/api/login", {"username": "test_editor", "password": new_password})
        self.assertEqual(status, 200)
        status, body, _ = self.request("GET", "/api/users", headers=admin)
        self.assertEqual(status, 200)
        self.assertNotIn(new_password, body.decode())
        self.assertNotIn("password_hash", body.decode())
        self.assertEqual(self.request("POST", "/api/users/liruiqi/reset-password", {"reset": True}, admin)[0], 400)
        self.assertEqual(self.request("POST", "/api/password", {"old_password": "wrong-original", "new_password": "Synthetic-Own-New-456"}, admin)[0], 400)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=admin)[0], 200)
        self.assertEqual(self.request("POST", "/api/password", {"old_password": self.password, "new_password": "Synthetic-Own-New-456"}, admin)[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/items", headers=admin)[0], 401)

    def test_editor_can_complete_without_chat_result_with_manual_audit(self):
        editor = self.login("test_editor")
        status, _, _ = self.request("PATCH", "/api/items/SYNTHETIC-001", {"status": "已解决"}, editor)
        self.assertEqual(status, 400)
        changes = {"status": "已解决", "closure_note": "虚构员工现场已处理，微信群尚未反馈", "closure_scope": "仅虚构处理节点"}
        status, body, _ = self.request("PATCH", "/api/items/SYNTHETIC-001", changes, editor)
        self.assertEqual(status, 200)
        item = json.loads(body)["item"]
        self.assertEqual(item["review_state"], "员工手工补录完成")
        self.assertEqual(item["completed_by"], "test_editor")
        self.assertEqual(item["completed_by_name"], "虚构处理人员")
        self.assertTrue(item["evidence"][-1]["locator"].startswith("manual:"))
        self.assertEqual(item["evidence"][-1]["sender"], "虚构处理人员")
        self.store.import_snapshot(self.snapshot)
        self.assertEqual(self.store.snapshot()["items"][0]["status"], "已解决")

    def test_closed_scope_change_requires_new_admin_evidence(self):
        self.store.update_item("SYNTHETIC-001", {"status": "已解决", "closure_note": "第一份虚构确认依据", "closure_scope": "第一阶段"}, "liruiqi")
        with self.assertRaises(ValueError):
            self.store.update_item("SYNTHETIC-001", {"closure_scope": "第二阶段"}, "liruiqi")
        item = self.store.update_item("SYNTHETIC-001", {"closure_scope": "第二阶段", "closure_note": "第二份虚构确认依据"}, "liruiqi")
        self.assertEqual(item["closure_scope"], "第二阶段")
        self.assertEqual(item["evidence"][-1]["text"], "第二份虚构确认依据")
        self.assertEqual(item["review_state"], "李睿琪已复核")

    def test_manual_edits_preserved_on_reimport_and_reported_today(self):
        with patch("app.store.now", return_value="2026-10-12T16:00:00+08:00"):
            self.store.update_item("SYNTHETIC-001", {"owner": "虚构人工负责人", "deadline": "2026-10-14", "status": "处理中"}, "liruiqi")
        incoming = copy.deepcopy(self.snapshot)
        incoming["items"][0]["owner"] = "虚构自动负责人"
        incoming["items"][0]["status"] = "待确认"
        self.store.import_snapshot(incoming)
        item = self.store.snapshot()["items"][0]
        self.assertEqual(item["owner"], "虚构人工负责人")
        self.assertEqual(item["deadline"], "2026-10-14")
        self.assertEqual(item["status"], "处理中")
        self.assertEqual(item["source_last_seen"], "2026-10-07T10:00:00+08:00")
        report = build_report(self.store.snapshot(), "daily", "2026-10-12")
        self.assertIn("SYNTHETIC-001", {x["id"] for x in report["sections"]["updated"]})
        self.assertEqual(len(item["evidence"]), 1)

    def test_manual_closure_preserved_and_absent_older_items_retained(self):
        self.store.update_item("SYNTHETIC-001", {"status": "已解决", "closure_note": "虚构人工关闭依据"}, "liruiqi")
        self.store.import_snapshot(self.snapshot)
        item = self.store.snapshot()["items"][0]
        self.assertEqual(item["status"], "已解决")
        self.assertEqual(item["evidence"][-1]["text"], "虚构人工关闭依据")
        self.store.update_item("SYNTHETIC-001", {"status": "待验证"}, "liruiqi")
        empty = copy.deepcopy(self.snapshot)
        empty["items"] = []
        self.store.import_snapshot(empty)
        self.assertEqual(self.store.snapshot()["items"][0]["status"], "待验证")

    def test_liruiqi_can_register_own_drawing_task(self):
        admin = self.login()
        status, body, _ = self.request("POST", "/api/items", {"title": "虚构机械绘图任务", "category": "任务", "owner": "副厂（本人）", "summary": "虚构说明"}, admin)
        self.assertEqual(status, 201)
        item = json.loads(body)["item"]
        self.assertEqual(item["source_id"], "manual")
        self.assertEqual(item["owner"], "副厂（本人）")
        self.assertEqual(item["status"], "待处理")
        self.store.import_snapshot(self.snapshot)
        self.assertIn(item["id"], {x["id"] for x in self.store.snapshot()["items"]})

    def test_private_source_and_path_traversal_not_served(self):
        admin = self.login()
        for path in ("/private/production_V0.2.0.sqlite3", "/private/初始账号_V0.2.0.txt",
                     "/../private/production_V0.2.0.sqlite3", "/%2e%2e/private/production_V0.2.0.sqlite3",
                     "/acquisition/source.json", "/app/../private/production_V0.2.0.sqlite3"):
            with self.subTest(path=path):
                # 中文路径先编码，测试只检查服务没有开放该目录。
                from urllib.parse import quote
                self.assertEqual(self.request("GET", quote(path, safe="/%"), headers=admin)[0], 404)


if __name__ == "__main__":
    unittest.main()

