"""生产资料 HTTP 权限和文件路由：隔离库、合成账号，不读业务数据或真凭据。"""

import hashlib
import http.client
import json
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote, unquote

from app.server import DashboardServer
from app.store import Store, now, password_hash
from tests.test_store_server import ResponseHeaders


class ProductionDocumentRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="synthetic-production-document-routes-")
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.password = "Synthetic-Document-Only-123"
        self.store.create_user("synthetic_editor", "虚构编辑人员", "editor")
        self.store.create_user("synthetic_viewer", "虚构查看人员", "viewer")
        with self.store.connect() as connection:
            for username in ("liruiqi", "synthetic_editor", "synthetic_viewer"):
                salt = connection.execute("SELECT salt FROM users WHERE username=?", (username,)).fetchone()[0]
                connection.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE username=?",
                                   (password_hash(self.password, salt), username))
        self.server = DashboardServer(("127.0.0.1", 0), self.store, "192.168.10.237")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.order_id = self.server.production.add_order(
            {"title": "虚构生产单", "order_no": "SYNTHETIC-DOCUMENT-001"}, "liruiqi")["id"]
        self.server.production.add_piece(
            {"order_id": self.order_id, "piece_label": "虚构01", "product_name": "虚构测试模型"}, "liruiqi")
        self.production_before = self.server.production.snapshot()
        self.document_id = "PODOC-" + "a" * 32
        timestamp = now()
        source = {
            "id": self.document_id, "order_id": self.order_id, "filename": "虚构生产单.xlsx",
            "sha256": "b" * 64, "source_ref": "synthetic!B8", "created_at": timestamp,
            "fields": {"order_no": "SYNTHETIC-DOCUMENT-001", "buyer": "虚构客户", "followup": ""},
            "field_sources": {"buyer": "Sheet1!A2"},
            "products": [{"name": "虚构测试模型", "quantity": 1, "unit": "个", "specification": "",
                          "piece_specifications": [""], "motions": "", "source_ref": "Sheet1!B8"}],
            "checks": [{"code": "missing_followup", "message": "原单未填写跟单员，保持空白"}],
            "totals_by_unit": {"个": 1},
        }
        with self.store.connect() as connection:
            connection.execute("INSERT INTO production_order_documents VALUES(?,?,?,?,?,?,?,?)",
                               (self.document_id, self.order_id, source["filename"], source["sha256"],
                                source["source_ref"], timestamp, "production_order_sources/synthetic.xlsx",
                                json.dumps(source, ensure_ascii=False)))
        self.output_id = "c" * 32
        folder = self.server.production_documents.base / self.output_id
        folder.mkdir(parents=True)
        self.file_bytes = {
            "xlsx": b"PK\x03\x04synthetic-route-fixture-only",
            "pdf": b"%PDF-1.4\nsynthetic-route-fixture-only\n%%EOF",
            "preview_1": b"\x89PNG\r\n\x1a\nsynthetic-cached-route-fixture-only",
        }
        self.files = {}
        for kind, data in self.file_bytes.items():
            filename = {"xlsx": "synthetic.xlsx", "pdf": "synthetic.pdf", "preview_1": "page-1.png"}[kind]
            path = folder / filename
            path.write_bytes(data)
            self.files[kind] = path
        self.output = {
            "id": self.output_id, "order_id": self.order_id, "document_id": self.document_id,
            "kind": "labels", "batch": 1, "filename": "虚构产品标签_V001.xlsx", "created_at": timestamp,
            "count": 1, "warnings": [], "pdf_ready": True, "preview_ready": True, "pages": 1, "status": "ready",
            "files": {kind: {"path": str(path.relative_to(self.server.production_documents.base)),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                      for kind, path in self.files.items()},
        }
        with self.store.connect() as connection:
            connection.execute("INSERT INTO documents_generated VALUES(?,?,?,?,?,?)",
                               (self.output_id, self.order_id, "labels", 1, timestamp,
                                json.dumps(self.output, ensure_ascii=False)))

    def tearDown(self):
        # 查看、生成、下载资料都不得改变工序、交付状态及其更新记录。
        self.assertEqual(self.server.production.snapshot(), self.production_before)

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
            return response.status, response.read(), ResponseHeaders(response.getheaders())
        finally:
            connection.close()

    def login(self, username="liruiqi"):
        status, body, headers = self.request("POST", "/api/login", {"username": username, "password": self.password})
        self.assertEqual(status, 200)
        return {"Cookie": "dino_session=" + headers.cookies["dino_session"].value, "X-CSRF-Token": json.loads(body)["csrf"]}

    def detail_path(self, order_id=None):
        return f"/api/production/orders/{order_id or self.order_id}/documents"

    def generate_path(self, order_id=None):
        return self.detail_path(order_id) + "/generate"

    def file_path(self, output_id=None, format="xlsx"):
        return f"/api/production/document-files/{output_id or self.output_id}?format={format}"

    def page_path(self, page=1, output_id=None):
        return f"/api/production/document-files/{output_id or self.output_id}/pages/{page}.png"

    def assert_error(self, result, expected_status):
        status, body, headers = result
        self.assertEqual(status, expected_status, body.decode("utf-8", "replace"))
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        error = json.loads(body)
        self.assertIsInstance(error.get("error"), str)
        self.assertTrue(error["error"].strip())
        return error

    def test_anonymous_cannot_read_generate_download_or_preview(self):
        with patch.object(self.server.production_documents, "generate") as generate:
            for path in (self.detail_path(), self.file_path(), self.file_path(format="pdf"), self.page_path()):
                with self.subTest(path=path):
                    self.assert_error(self.request("GET", path), 401)
            self.assert_error(self.request("POST", self.generate_path(), {"kind": "labels"}), 401)
            generate.assert_not_called()

    def test_initial_password_requires_change_for_every_document_route(self):
        with self.store.connect() as connection:
            connection.execute("UPDATE users SET must_change_password=1 WHERE username='synthetic_editor'")
        actor = self.login("synthetic_editor")
        for method, path, payload in (
            ("GET", self.detail_path(), None), ("GET", self.file_path(), None),
            ("GET", self.page_path(), None), ("POST", self.generate_path(), {"kind": "labels"}),
        ):
            with self.subTest(path=path):
                error = self.assert_error(self.request(method, path, payload, actor), 403)
                self.assertEqual(error["code"], "password_change_required")

    def test_personal_api_token_is_read_only(self):
        actor = self.login()
        status, body, _ = self.request("POST", "/api/tokens", {"label": "虚构资料测试令牌", "days": 7}, actor)
        self.assertEqual(status, 201)
        bearer = {"Authorization": "Bearer " + json.loads(body)["token"]}
        self.assertEqual(self.request("GET", self.detail_path(), headers=bearer)[0], 200)
        self.assertEqual(self.request("GET", self.file_path(), headers=bearer)[0], 200)
        with patch.object(self.server.production_documents, "generate") as generate:
            self.assert_error(self.request("POST", self.generate_path(), {"kind": "labels"}, bearer), 403)
            generate.assert_not_called()

    def test_generation_requires_csrf_and_trusted_origin(self):
        actor = self.login()
        invalid_headers = [
            {"Cookie": actor["Cookie"]},
            {**actor, "X-CSRF-Token": "synthetic-wrong-csrf"},
            {**actor, "Origin": "https://untrusted.synthetic.invalid"},
            {**actor, "Host": "untrusted.synthetic.invalid"},
        ]
        with patch.object(self.server.production_documents, "generate") as generate:
            for headers in invalid_headers:
                with self.subTest(headers=list(headers)):
                    self.assert_error(self.request("POST", self.generate_path(), {"kind": "labels"}, headers), 403)
            generate.assert_not_called()

    def test_viewer_can_read_but_cannot_generate(self):
        viewer = self.login("synthetic_viewer")
        self.assertEqual(self.request("GET", self.detail_path(), headers=viewer)[0], 200)
        self.assertEqual(self.request("GET", self.file_path(), headers=viewer)[0], 200)
        with patch("app.production_documents._convert_pdf") as render:
            self.assert_error(self.request("POST", self.generate_path(), {"kind": "labels"}, viewer), 403)
            render.assert_not_called()

    def test_details_exposes_source_fields_and_public_history_without_private_paths(self):
        status, body, _ = self.request("GET", self.detail_path(), headers=self.login())
        self.assertEqual(status, 200)
        detail = json.loads(body)
        self.assertEqual(detail["latest"]["fields"]["buyer"], "虚构客户")
        self.assertEqual(detail["latest"]["products"][0]["source_ref"], "Sheet1!B8")
        self.assertEqual(detail["latest"]["totals_by_unit"], {"个": 1})
        self.assertEqual(detail["history"][0]["id"], self.output_id)
        self.assertNotIn("files", detail["history"][0])
        self.assertNotIn("storage_path", body.decode())
        self.assertNotIn(str(self.server.production_documents.base), body.decode())

    def test_download_and_cached_png_bytes_headers_and_no_renderer(self):
        actor = self.login()
        cases = [
            (self.file_path(), "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "attachment"),
            (self.file_path(format="pdf"), "pdf", "application/pdf", "attachment"),
            (self.file_path(format="pdf") + "&inline=1", "pdf", "application/pdf", "inline"),
            (self.page_path(), "preview_1", "image/png", "inline"),
        ]
        with patch("app.production_documents._convert_pdf") as render:
            for path, kind, mime, disposition in cases:
                with self.subTest(path=path):
                    status, body, headers = self.request("GET", path, headers=actor)
                    self.assertEqual(status, 200)
                    self.assertEqual(body, self.file_bytes[kind])
                    self.assertTrue(headers["Content-Type"].startswith(mime))
                    self.assertTrue(headers["Content-Disposition"].startswith(disposition))
                    self.assertEqual(int(headers["Content-Length"]), len(body))
                    self.assertEqual(headers["Connection"], "close")
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            render.assert_not_called()

    def test_chinese_parenthesized_filename_has_ascii_fallback_and_utf8_name(self):
        filename = "产品质检表（试跑）(A B)_V001.xlsx"
        self.output["filename"] = filename
        with self.store.connect() as connection:
            connection.execute("UPDATE documents_generated SET data_json=? WHERE id=?",
                               (json.dumps(self.output, ensure_ascii=False), self.output_id))
        status, body, headers = self.request("GET", self.file_path(), headers=self.login())
        self.assertEqual(status, 200)
        disposition = headers["Content-Disposition"]
        self.assertEqual(disposition, 'attachment; filename="production-document.xlsx"; filename*=UTF-8\'\'' + quote(filename, safe=""))
        self.assertEqual(unquote(disposition.split("filename*=UTF-8''", 1)[1]), filename)
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(body, self.file_bytes["xlsx"])

    def test_download_lengths_and_tcp_eof_even_when_keep_alive_requested(self):
        actor = self.login()
        # 接近问题截图的两种大小，只用临时目录内的合成内容。
        for size in (166000, 6042):
            with self.subTest(size=size):
                payload = (b"PK\x03\x04synthetic-transfer-fixture\n" * (size // 30 + 2))[:size]
                self.assertEqual(len(payload), size)
                self.files["xlsx"].write_bytes(payload)
                self.output["files"]["xlsx"]["sha256"] = hashlib.sha256(payload).hexdigest()
                with self.store.connect() as connection:
                    connection.execute("UPDATE documents_generated SET data_json=? WHERE id=?",
                                       (json.dumps(self.output, ensure_ascii=False), self.output_id))
                with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2) as connection:
                    connection.settimeout(2)
                    request = (f"GET {self.file_path()} HTTP/1.1\r\n"
                               f"Host: 127.0.0.1:{self.server.server_port}\r\n"
                               f"Cookie: {actor['Cookie']}\r\nConnection: keep-alive\r\n\r\n")
                    connection.sendall(request.encode("ascii"))
                    chunks = []
                    while True:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break  # 必须实际观察 TCP EOF，而非只按 Content-Length 停止读取。
                        chunks.append(chunk)
                header_bytes, body = b"".join(chunks).split(b"\r\n\r\n", 1)
                lines = header_bytes.decode("latin-1").splitlines()
                headers = {key.lower(): value.strip() for key, value in (line.split(":", 1) for line in lines[1:])}
                self.assertEqual(lines[0], "HTTP/1.0 200 OK")
                self.assertEqual(headers["connection"], "close")
                self.assertEqual(int(headers["content-length"]), size)
                self.assertEqual(body, payload)

    def test_valid_generation_dispatches_one_kind_only_and_keeps_progress(self):
        actor = self.login("synthetic_editor")
        with patch.object(self.server.production_documents, "generate", return_value={"id": "mock-output", "status": "ready"}) as generate:
            for kind in ("labels", "fitter", "product"):
                with self.subTest(kind=kind):
                    status, body, _ = self.request("POST", self.generate_path(), {"kind": kind}, actor)
                    self.assertEqual(status, 201)
                    self.assertEqual(json.loads(body)["status"], "ready")
                    generate.assert_called_with(self.order_id, kind, "synthetic_editor")
            self.assertEqual(generate.call_count, 3)

    def test_invalid_kinds_and_extra_fields_are_clear_client_errors(self):
        actor = self.login()
        invalid = [{}, {"kind": None}, {"kind": ""}, {"kind": "unknown"}, {"kind": []},
                   {"kind": {}}, {"kind": 1}, {"kind": "labels", "path": "synthetic.xlsx"},
                   {"kind": "labels", "stage": "合格"}, {"kind": "labels", "document_id": self.document_id}]
        with patch("app.production_documents._convert_pdf") as render:
            for data in invalid:
                with self.subTest(data=data):
                    self.assert_error(self.request("POST", self.generate_path(), data, actor), 400)
            render.assert_not_called()

    def test_missing_order_output_and_out_of_range_page_are_clear_errors(self):
        actor = self.login()
        self.assert_error(self.request("GET", self.detail_path("ORDER-NOT-EXIST"), headers=actor), 404)
        missing = "d" * 32
        for path in (self.file_path(missing), self.page_path(output_id=missing), self.page_path(page=2),
                     self.page_path(page=0), self.page_path(page=-1)):
            with self.subTest(path=path):
                self.assert_error(self.request("GET", path, headers=actor), 404)
        self.assert_error(self.request("POST", self.generate_path("ORDER-NOT-EXIST"), {"kind": "labels"}, actor), 404)

    def test_invalid_ids_and_formats_cannot_escape_private_output_root(self):
        actor = self.login()
        for output_id in ("not-an-output", quote("../../synthetic-secret", safe=""), quote("..\\synthetic-secret", safe="")):
            with self.subTest(output_id=output_id):
                self.assert_error(self.request("GET", self.file_path(output_id), headers=actor), 404)
        self.assert_error(self.request("GET", self.file_path(format="html"), headers=actor), 404)
        tampered = dict(self.output)
        tampered["files"] = {"xlsx": {"path": "../synthetic-outside.xlsx", "sha256": "0" * 64}}
        with self.store.connect() as connection:
            connection.execute("UPDATE documents_generated SET data_json=? WHERE id=?",
                               (json.dumps(tampered), self.output_id))
        self.assert_error(self.request("GET", self.file_path(), headers=actor), 404)

    def test_modified_download_or_preview_file_is_rejected_by_hash(self):
        actor = self.login()
        for kind, path in (("xlsx", self.file_path()), ("preview_1", self.page_path())):
            with self.subTest(kind=kind):
                self.files[kind].write_bytes(b"synthetic-tampered-file")
                self.assert_error(self.request("GET", path, headers=actor), 404)


if __name__ == "__main__":
    unittest.main()
