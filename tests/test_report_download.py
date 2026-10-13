"""仅使用临时库、虚构事项及隔离端口验证下载响应，不读取正式账号。"""

import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from app import __version__
from app.server import COOKIE, DashboardServer
from app.store import Store
from test_store_server import synthetic_snapshot


class ReportDownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dino-report-download-")
        self.addCleanup(self.directory.cleanup)
        store = Store(self.directory.name)
        store.import_snapshot(synthetic_snapshot())
        created = store.create_user("download_viewer", "虚构下载测试人员", "viewer")
        password = "Synthetic-Download-123"
        store.change_password("download_viewer", created["initial_password"], password)
        session = store.login("download_viewer", password)
        self.headers = {"Cookie": COOKIE + "=" + session["token"]}
        self.server = DashboardServer(("127.0.0.1", 0), store, "192.168.10.237")
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, kind="daily", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET", f"/api/reports?kind={kind}&end=2026-10-12&format=pdf",
                               headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_daily_and_weekly_pdf_are_named_download_attachments(self):
        pdf = b"%PDF-1.4\nsynthetic-download-response\n%%EOF"
        with patch("app.pdf_export.render_report_pdf", return_value=pdf) as render:
            for kind, label in (("daily", "日报"), ("weekly", "周报")):
                with self.subTest(kind=kind):
                    status, body, headers = self.request(kind, self.headers)
                    self.assertEqual(status, 200)
                    self.assertEqual(body, pdf)
                    self.assertEqual(headers["Content-Type"], "application/pdf")
                    self.assertEqual(headers["Content-Length"], str(len(pdf)))
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    disposition, *parameters = headers["Content-Disposition"].split(";")
                    parameters = dict(parameter.strip().split("=", 1) for parameter in parameters)
                    self.assertEqual(disposition.strip(), "attachment")
                    self.assertEqual(parameters["filename"].strip('"'),
                                     f"production_{kind}_2026-10-12_V{__version__}.pdf")
                    self.assertTrue(parameters["filename*"].startswith("UTF-8''"))
                    self.assertEqual(unquote(parameters["filename*"][7:]),
                                     f"生产管理_{label}_2026-10-12_V{__version__}.pdf")
            self.assertEqual(render.call_count, 2)

    def test_anonymous_pdf_request_returns_json_401_without_rendering(self):
        with patch("app.pdf_export.render_report_pdf") as render:
            status, body, headers = self.request()
        self.assertEqual(status, 401)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("error", json.loads(body))
        self.assertNotIn("Content-Disposition", headers)
        render.assert_not_called()

    def test_pdf_failure_returns_json_503_without_attachment(self):
        with patch("app.pdf_export.render_report_pdf", side_effect=RuntimeError("虚构PDF导出失败")):
            status, body, headers = self.request(headers=self.headers)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "虚构PDF导出失败")
        self.assertNotIn("Content-Disposition", headers)


if __name__ == "__main__":
    unittest.main()
