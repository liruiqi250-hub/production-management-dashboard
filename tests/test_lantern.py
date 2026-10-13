"""仅临时库和虚构材料的独立部署、权限、进度与导出验证。"""
import base64
import csv
import http.client
import io
import json
import tempfile
import threading
import unittest
import zipfile
from xml.etree import ElementTree as ET

from app.lantern_server import LanternServer
from app.lantern_store import LanternStore
from app.store import Store, password_hash


class LanternTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lantern-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.store = LanternStore(self.temp.name)
        self.password = "Synthetic-Password-123"
        self.store.create_user("viewer_test", "虚构查看人员", "viewer")
        self.store.create_user("editor_test", "虚构处理人员", "editor")
        with self.store.connect() as conn:
            for row in conn.execute("SELECT username,salt FROM users").fetchall():
                conn.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE username=?",
                             (password_hash(self.password, row["salt"]), row["username"]))
        self.server = LanternServer(("127.0.0.1", 0), self.store, "127.0.0.1")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, data=None, headers=None):
        headers = dict(headers or {})
        if data is not None:
            data = json.dumps(data)
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def login(self, username="jianghong"):
        status, headers, content = self.request("POST", "/api/login", {"username": username, "password": self.password})
        self.assertEqual(status, 200)
        self.assertTrue(headers["Set-Cookie"].startswith("lantern_session="))
        return {"Cookie": headers["Set-Cookie"].split(";")[0], "X-CSRF-Token": json.loads(content)["csrf"]}

    def test_independent_database_and_cookie(self):
        original = Store(self.temp.name)
        self.assertNotEqual(original.path, self.store.path)
        self.assertEqual(self.store.path.name, "lantern.sqlite3")
        self.store.save_project({"name": "虚构项目", "products": []}, "jianghong")
        self.assertEqual(original.snapshot()["items"], [])
        self.assertEqual(len(original.users()), 2)
        credentials = self.login()
        credentials["Cookie"] = credentials["Cookie"].replace("lantern_session=", "dino_session=")
        self.assertEqual(self.request("GET", "/api/lantern", headers=credentials)[0], 401)

    def test_first_password_gate_and_no_production_routes(self):
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET must_change_password=1 WHERE username='jianghong'")
        credentials = self.login()
        self.assertEqual(self.request("GET", "/api/me", headers=credentials)[0], 200)
        self.assertEqual(self.request("GET", "/api/lantern", headers=credentials)[0], 403)
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET must_change_password=0 WHERE username='jianghong'")
        for path in ("/api/dashboard", "/api/v1/overview", "/api/after-sales"):
            self.assertEqual(self.request("GET", path, headers=credentials)[0], 404)
        self.assertEqual(self.request("GET", "/api/jobs/current", headers=credentials)[0], 200)
        self.assertEqual(self.request("POST", "/api/jobs", {}, credentials)[0], 400)

    def test_roles_readonly_tokens_csrf_and_host(self):
        viewer, editor = self.login("viewer_test"), self.login("editor_test")
        data = {"name": "虚构项目"}
        self.assertEqual(self.request("POST", "/api/lantern/projects", data, viewer)[0], 403)
        self.assertEqual(self.request("POST", "/api/lantern/projects", data, {"Cookie": editor["Cookie"]})[0], 403)
        self.assertEqual(self.request("POST", "/api/lantern/projects", data, {**editor, "Origin": "http://example.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/lantern/projects", data, editor)[0], 201)
        self.assertEqual(self.request("POST", "/api/users", {"username": "other_test", "display_name": "虚构", "role": "viewer"}, editor)[0], 403)
        token = self.store.create_api_token("jianghong", "Synthetic read test", 7)
        bearer = {"Authorization": "Bearer " + token["token"]}
        self.assertEqual(self.request("GET", "/api/lantern", headers=bearer)[0], 200)
        self.assertEqual(self.request("POST", "/api/lantern/projects", data, bearer)[0], 403)
        self.assertEqual(self.request("GET", "/health", headers={"Host": "example.invalid"})[0], 403)

    def test_actual_progress_basis_revision_conflict_and_field_validation(self):
        credentials = self.login()
        data = {"name": "虚构项目", "products": [{"name": "虚构产品", "stages": {"丝架": {"status": "已完成"}}}]}
        self.assertEqual(self.request("POST", "/api/lantern/projects", data, credentials)[0], 400)
        data["products"][0]["stages"]["丝架"]["note"] = "虚构测试已核对"
        status, _, body = self.request("POST", "/api/lantern/projects", data, credentials)
        self.assertEqual(status, 201)
        project = json.loads(body)["project"]
        self.assertEqual(project["products"][0]["stages"]["装灯"]["status"], "未开始")
        path = "/api/lantern/projects/" + project["id"]
        self.assertEqual(self.request("PATCH", path, {"notes": "第一人更新", "revision": 1}, credentials)[0], 200)
        self.assertEqual(self.request("PATCH", path, {"notes": "第二人过时更新", "revision": 1}, credentials)[0], 409)
        self.assertEqual(self.store.snapshot()["projects"][0]["notes"], "第一人更新")
        self.assertEqual(self.request("PATCH", path, {"revision": 2, "updated_by": "someone"}, credentials)[0], 400)

    def test_upload_validation_retention_and_authorized_download(self):
        credentials = self.login()
        data = {"filename": "虚构生产单.csv", "content_base64": base64.b64encode(b"synthetic-only").decode()}
        status, _, body = self.request("POST", "/api/lantern/orders/upload", data, credentials)
        self.assertEqual(status, 201)
        path = "/api/lantern/orders/" + json.loads(body)["attachment"]["id"]
        self.assertEqual(self.request("GET", path)[0], 401)
        self.assertEqual(self.request("GET", path, headers=credentials)[2], b"synthetic-only")
        for filename in ("../secret.csv", "evil.exe", "bad\r\n.csv", "CON.csv"):
            self.assertEqual(self.request("POST", "/api/lantern/orders/upload", {**data, "filename": filename}, credentials)[0], 400)
        self.assertEqual(self.request("POST", "/api/lantern/orders/upload", {**data, "content_base64": "invalid%"}, credentials)[0], 400)

    def test_export_exact_headers_no_formulas_and_date_validation(self):
        self.store.save_shipment({"customer": "=1+1", "documents": "虚构：待确认", "etd": "2026-10-20"}, "jianghong")
        rows = list(csv.reader(io.StringIO(self.store.export("shipments").decode("utf-8-sig"))))
        self.assertEqual(rows[0][0], "序号")
        self.assertIn("清关资料、电放提单提供情况", rows[0])
        self.assertIn("是否清关派送", rows[0])
        self.assertIn("'=1+1", rows[1])
        with zipfile.ZipFile(io.BytesIO(self.store.export("shipments", xlsx=True))) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertIsNone(sheet.find(".//{*}f"))
        with self.assertRaises(ValueError):
            self.store.save_shipment({"customer": "虚构", "eta": "2026-02-30"}, "jianghong")
        with self.assertRaises(ValueError):
            self.store.save_shipment({"customer": "虚构", "project_id": "missing"}, "jianghong")

    def test_fresh_accounts_not_inherited_or_reset(self):
        with tempfile.TemporaryDirectory(prefix="lantern-new-") as folder:
            fresh = LanternStore(folder)
            self.assertEqual({u["username"]: u["role"] for u in fresh.users()},
                             {"jianghong": "admin", "huangyumeng": "editor", "tianxiuling": "editor"})
            self.assertTrue(all(u["must_change_password"] for u in fresh.users()))
            note = fresh.private / "彩灯初始账号.txt"
            before = note.read_bytes()
            LanternStore(folder)
            self.assertEqual(note.read_bytes(), before)

    def test_document_extraction_and_explicit_confirmation_gate(self):
        credentials = self.login()
        data = {'kind':'packing','filename':'synthetic.csv','content_base64':base64.b64encode(
            '名称,件数,毛重(kg),净重(kg),体积(CBM)\n虚构,1,80,32,1.12\n总计,1,80,32,1.12'.encode()).decode()}
        status, _, body = self.request('POST','/api/lantern/documents/extract',data,credentials)
        self.assertEqual(status,200)
        result = json.loads(body)
        self.assertEqual(result['fields']['packages'],'1')
        self.assertEqual(result['fields']['volume'],'1.12')
        self.assertTrue(result['attachment_id'])
        self.assertEqual(self.request('POST','/api/lantern/documents/generate',
                                     {'template_id':'dayang','fields':{},'source_ids':[],'confirmed':False},credentials)[0],400)
        viewer = self.login('viewer_test')
        self.assertEqual(self.request('POST','/api/lantern/documents/extract',data,viewer)[0],403)
