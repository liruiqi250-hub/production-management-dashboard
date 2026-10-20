"""生产资料打印HTTP验收：临时服务器与合成PDF，禁止提交真实打印任务。"""
import hashlib
from html import escape
import importlib.util
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import uuid

from app.production_documents import ArtifactsExpired
from app.production_printing import PRINTER_NAME
from tests import test_production_document_routes as route_helpers


class ProductionPrintRouteTests(unittest.TestCase):
    # 仅复用夹具方法，不继承旧测试类，避免再执行旧的14个测试。
    request = route_helpers.ProductionDocumentRouteTests.request
    login = route_helpers.ProductionDocumentRouteTests.login
    close_server = route_helpers.ProductionDocumentRouteTests.close_server
    file_path = route_helpers.ProductionDocumentRouteTests.file_path
    page_path = route_helpers.ProductionDocumentRouteTests.page_path
    detail_path = route_helpers.ProductionDocumentRouteTests.detail_path
    assert_error = route_helpers.ProductionDocumentRouteTests.assert_error
    tearDown = route_helpers.ProductionDocumentRouteTests.tearDown

    def setUp(self):
        # 连启动也隔离；所有HTTP提交/状态/单任务调用都使用mock，绝不碰打印驱动。
        self.worker_patch = patch("app.production_printing.ProductionPrinting.start")
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.status_patch = patch("app.production_printing.ProductionPrinting.status", return_value={
            "enabled": True, "available": True, "printer_name": PRINTER_NAME, "notice": "合成测试，不会实际打印"})
        self.status_mock = self.status_patch.start()
        self.addCleanup(self.status_patch.stop)
        self.submit_patch = patch("app.production_printing.ProductionPrinting.submit")
        self.submit_mock = self.submit_patch.start()
        self.addCleanup(self.submit_patch.stop)
        self.get_patch = patch("app.production_printing.ProductionPrinting.get")
        self.get_mock = self.get_patch.start()
        self.addCleanup(self.get_patch.stop)
        route_helpers.ProductionDocumentRouteTests.setUp(self)
        self.make_pdf(3)
        self.job_id = "e" * 32
        self.job = {"id": self.job_id, "output_id": self.output_id, "state": "queued", "pages": [1, 2],
                    "copies": 1, "total_sheets": 2, "printer_name": PRINTER_NAME,
                    "message": "合成任务，未提交打印机"}
        self.submit_mock.return_value = self.job
        self.get_mock.return_value = self.job

    def make_pdf(self, pages):
        from pypdf import PdfWriter
        writer = PdfWriter()
        for number in range(pages):
            writer.add_blank_page(width=160 if number == 1 else 100, height=100 if number == 1 else 120)
        writer.write(str(self.files["pdf"]))
        self.file_bytes["pdf"] = self.files["pdf"].read_bytes()
        self.output["files"]["pdf"]["sha256"] = hashlib.sha256(self.file_bytes["pdf"]).hexdigest()
        self.output["pages"] = pages
        self.save_output()

    def save_output(self):
        with self.store.connect() as connection:
            connection.execute("UPDATE documents_generated SET data_json=? WHERE id=?",
                               (json.dumps(self.output, ensure_ascii=False), self.output_id))

    def print_path(self, pages=None):
        result = f"/api/production/document-files/{self.output_id}/print"
        return result + "?" + urlencode({"pages": pages}) if pages is not None else result

    def submit_data(self, **extra):
        return {"output_id": self.output_id, "pages": "1-2", "copies": 1, "request_id": str(uuid.uuid4()), **extra}

    def bearer(self):
        status, body, _ = self.request("POST", "/api/tokens", {"label": "合成打印测试令牌", "days": 7}, self.login())
        self.assertEqual(status, 201)
        return {"Authorization": "Bearer " + json.loads(body)["token"]}

    def test_viewer_print_html_uses_real_pdf_pages_and_does_not_submit_job(self):
        actor = self.login("synthetic_viewer")
        # PDF有三页，即使旧metadata计数不准，也应从已校验的真实PDF读取。
        self.output["pages"] = 999
        self.save_output()
        status, body, headers = self.request("GET", self.print_path(), headers=actor)
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertIn("object-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("本次准备 3 页", text)
        self.assertEqual(text.count('<section class="print-sheet'), 3)
        self.assertIn('class="print-sheet landscape"', text)
        for page in range(1, 4):
            self.assertIn(f"/pages/{page}.png?quality=print", text)
        self.assertIn("data-print-src=", text)
        self.assertNotIn("<object", text)
        self.submit_mock.assert_not_called()

    def test_print_html_page_selection_is_sorted_deduplicated_and_escaped(self):
        filename = '<img src=x onerror="bad()"> & <script>bad</script>.xlsx'
        self.output["filename"] = filename
        self.save_output()
        status, body, _ = self.request("GET", self.print_path("3,1-2,2"), headers=self.login("synthetic_viewer"))
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn(escape(filename), text)
        self.assertNotIn(filename, text)
        self.assertNotIn("<script>bad", text)
        self.assertIn("原文件页码 1,2,3", text)
        self.assertLess(text.index("/pages/1.png"), text.index("/pages/2.png"))
        self.assertLess(text.index("/pages/2.png"), text.index("/pages/3.png"))

    def test_print_html_invalid_page_formats_and_oversize_batches_are_rejected(self):
        actor = self.login()
        for pages in ("0", "-1", "4", "3-1", "1-4", "1--2", "all", "1,,2", "1.0", "1<script>"):
            with self.subTest(pages=pages):
                self.assert_error(self.request("GET", self.print_path(pages), headers=actor), 404)
        self.make_pdf(81)
        error = self.assert_error(self.request("GET", self.print_path(), headers=actor), 404)
        self.assertIn("80页", error["error"])
        self.assertEqual(self.request("GET", self.print_path("1-3"), headers=actor)[0], 200)
        self.submit_mock.assert_not_called()

    def test_print_quality_uses_independent_higher_resolution_png_cache(self):
        if not (importlib.util.find_spec("pypdfium2") or importlib.util.find_spec("fitz")):
            self.skipTest("隔离环境没有PDF图片渲染组件")
        actor = self.login("synthetic_viewer")
        documents = self.server.production_documents
        with patch.object(documents, "preview_page", wraps=documents.preview_page) as preview:
            status, body, headers = self.request("GET", self.page_path() + "?quality=print", headers=actor)
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertTrue(headers["Content-Type"].startswith("image/png"))
            preview.assert_called_once_with(self.output_id, 1, print_quality=True)
        record = documents._get(self.output_id)
        self.assertIn("print_preview_1", record["files"])
        self.assertTrue(record["files"]["print_preview_1"]["path"].endswith("print-page-1.png"))
        self.assertEqual(self.request("GET", self.page_path(), headers=actor)[1], self.file_bytes["preview_1"])
        self.assertNotEqual(record["files"]["print_preview_1"]["path"], record["files"]["preview_1"]["path"])

    def test_anonymous_cannot_read_print_page_status_or_jobs_or_submit(self):
        for path in (self.print_path(), "/api/production/printing", "/api/production/print-jobs/" + "e" * 32):
            self.assert_error(self.request("GET", path), 401)
        self.assert_error(self.request("POST", "/api/production/print-jobs", self.submit_data()), 401)
        self.status_mock.assert_not_called()
        self.submit_mock.assert_not_called()
        self.get_mock.assert_not_called()

    def test_viewer_session_may_submit_but_cannot_choose_driver_or_printer(self):
        actor = self.login("synthetic_viewer")
        data = self.submit_data()
        status, body, _ = self.request("POST", "/api/production/print-jobs", data, actor)
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["id"], self.job_id)
        args, kwargs = self.submit_mock.call_args
        self.assertEqual(args[0], self.output_id)
        self.assertEqual(args[1]["username"], "synthetic_viewer")
        self.assertEqual(args[1]["auth_type"], "session")
        self.assertEqual(args[1]["role"], "viewer")
        self.assertEqual(kwargs, {"pages": "1-2", "copies": 1, "request_id": data["request_id"]})
        self.submit_mock.reset_mock()
        for key in ("printer_name", "printer", "driver", "command", "path", "username", "state"):
            with self.subTest(key=key):
                self.assert_error(self.request("POST", "/api/production/print-jobs", self.submit_data(**{key: "client-input"}), actor), 400)
        self.submit_mock.assert_not_called()

    def test_print_submission_requires_csrf_same_origin_and_session_not_token(self):
        actor = self.login()
        invalid = [{"Cookie": actor["Cookie"]}, {**actor, "X-CSRF-Token": "wrong"},
                   {**actor, "Origin": "https://untrusted.synthetic.invalid"},
                   {**actor, "Host": "untrusted.synthetic.invalid"}, self.bearer()]
        for headers in invalid:
            with self.subTest(headers=list(headers)):
                self.assert_error(self.request("POST", "/api/production/print-jobs", self.submit_data(), headers), 403)
        self.submit_mock.assert_not_called()

    def test_print_submission_defaults_and_service_client_errors_are_preserved(self):
        actor = self.login("synthetic_viewer")
        request_id = str(uuid.uuid4())
        status, _, _ = self.request("POST", "/api/production/print-jobs", {"output_id": self.output_id, "request_id": request_id}, actor)
        self.assertEqual(status, 202)
        self.assertEqual(self.submit_mock.call_args.kwargs, {"pages": "", "copies": 1, "request_id": request_id})
        self.submit_mock.side_effect = ValueError("页码超出合成PDF范围")
        error = self.assert_error(self.request("POST", "/api/production/print-jobs", self.submit_data(pages="4"), actor), 400)
        self.assertIn("页码", error["error"])

    def test_status_and_job_routes_forward_only_authenticated_actor(self):
        viewer, admin = self.login("synthetic_viewer"), self.login()
        self.assertEqual(self.request("GET", "/api/production/printing", headers=viewer)[0], 200)
        self.assertEqual(self.status_mock.call_count, 1)
        def private_job(job_id, actor):
            self.assertEqual(job_id, self.job_id)
            if actor["auth_type"] != "session":
                raise PermissionError("需要网页登录会话")
            if actor["username"] not in {"synthetic_editor", "liruiqi"}:
                raise PermissionError("只能查看自己的打印任务")
            return self.job
        self.get_mock.side_effect = private_job
        path = "/api/production/print-jobs/" + self.job_id
        self.assert_error(self.request("GET", path + "?username=liruiqi", headers=viewer), 403)
        self.assertEqual(self.get_mock.call_args.args[1]["username"], "synthetic_viewer")
        self.assertEqual(self.request("GET", path, headers=self.login("synthetic_editor"))[0], 200)
        self.assertEqual(self.get_mock.call_args.args[1]["username"], "synthetic_editor")
        self.assertEqual(self.request("GET", path, headers=admin)[0], 200)
        self.assertEqual(self.get_mock.call_args.args[1]["username"], "liruiqi")
        self.assert_error(self.request("GET", path, headers=self.bearer()), 403)

    def test_expired_download_print_page_preview_and_submit_return_410(self):
        actor = self.login("synthetic_viewer")
        self.output.update(status="cleaned", cleaned_at="2026-10-20T12:00:00+08:00", pdf_ready=False, preview_ready=False)
        self.save_output()
        for path in (self.file_path(), self.file_path(format="pdf"), self.print_path(), self.page_path(), self.page_path() + "?quality=print"):
            with self.subTest(path=path):
                error = self.assert_error(self.request("GET", path, headers=actor), 410)
                self.assertEqual(error["code"], "artifacts_expired")
        # 验证打印路由对服务层专用异常的转换；不调用任何真实打印实现。
        self.submit_mock.side_effect = ArtifactsExpired("临时资料已清理，请重新生成")
        error = self.assert_error(self.request("POST", "/api/production/print-jobs", self.submit_data(), actor), 410)
        self.assertEqual(error["code"], "artifacts_expired")

    def test_http_production_search_terms_include_source_buyer_without_mutating_master(self):
        actor = self.login("synthetic_viewer")
        before = self.server.production.snapshot()
        self.assertNotIn("search_terms", before["orders"][0])
        for path in ("/api/production", "/api/v1/production"):
            status, body, _ = self.request("GET", path, headers=actor)
            self.assertEqual(status, 200)
            orders = json.loads(body)["orders"]
            order = next(item for item in orders if item["id"] == self.order_id)
            self.assertIn("虚构客户", order["search_terms"])
            self.assertIn("虚构生产单.xlsx", order["search_terms"])
            self.assertEqual(len(order["pieces"]), 1)
        self.assertEqual(self.server.production.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
