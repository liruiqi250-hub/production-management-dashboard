"""静默巡检用真实本地HTTP验证快速路径，用隔离启动器验证恢复边界。"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.ensure_dashboard_running import ensure_running


class SilentStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="静默 startup ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "VERSION").write_text("1.2.3\n", encoding="utf-8-sig")

    def serve(self, payload):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *_):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def test_healthy_service_never_starts_shell_even_with_proxy(self):
        port = self.serve({"status": "ready", "version": "1.2.3", "workspace_id":
                           hashlib.sha256(str(self.root).lower().encode()).hexdigest()})
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}), \
                patch("scripts.ensure_dashboard_running.subprocess.run") as run:
            self.assertEqual(ensure_running(self.root, port), 0)
            run.assert_not_called()
        self.assertFalse((self.root / "private").exists())

    def test_foreign_or_old_service_is_not_touched(self):
        for payload in [{"status": "ready", "version": "0.1", "workspace_id": "other"}, []]:
            with self.subTest(payload=payload):
                port = self.serve(payload)
                with patch("scripts.ensure_dashboard_running.subprocess.run") as run:
                    self.assertEqual(ensure_running(self.root, port), 2)
                    run.assert_not_called()
                self.assertIn("现有服务未改动", (self.root / "private/startup_last_error.log").read_text("utf-8"))

    def test_unreachable_service_uses_no_console_with_no_browser(self):
        from urllib.error import URLError
        with patch("urllib.request.OpenerDirector.open", side_effect=URLError("offline")), \
                patch("scripts.ensure_dashboard_running.subprocess.run", return_value=
                      subprocess.CompletedProcess([], 0, b"", b"")) as run:
            self.assertEqual(ensure_running(self.root, 18769), 0)
        command = run.call_args.args[0]
        self.assertIn(str(self.root / "scripts/start_dashboard_V0.2.0.ps1"), command)
        self.assertIn("-NoBrowser", command)
        self.assertEqual(command[-2:], ["-Port", "18769"])
        if os.name == "nt":
            self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertNotIn("codex", command[0].lower())

    def test_repeated_failures_keep_only_bounded_last_error(self):
        def fail(*args, **kwargs):
            kwargs["stdout"].write(b"x" * 10000 + b"err")
            return subprocess.CompletedProcess([], 2)
        with patch("urllib.request.OpenerDirector.open", side_effect=OSError("offline")), \
                patch("scripts.ensure_dashboard_running.subprocess.run", side_effect=fail):
            for _ in range(3):
                self.assertEqual(ensure_running(self.root, 18769), 2)
        self.assertLess((self.root / "private/startup_last_error.log").stat().st_size, 6200)

    def test_timeout_is_reported_without_a_console_or_unbounded_retry(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=OSError("offline")), \
                patch("scripts.ensure_dashboard_running.subprocess.run", side_effect=
                      subprocess.TimeoutExpired("powershell.exe", 90)) as run:
            self.assertEqual(ensure_running(self.root, 18769), 1)
            self.assertEqual(run.call_count, 1)
        self.assertIn("TimeoutExpired", (self.root / "private/startup_last_error.log").read_text("utf-8"))

    @unittest.skipUnless(os.name == "nt", "验证Windows启动器子进程句柄继承")
    def test_recovery_check_exits_before_its_background_child(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        executable = sys.executable.replace("'", "''")
        (scripts / "start_dashboard_V0.2.0.ps1").write_text(
            "param([switch]$NoBrowser,[int]$Port)\n"
            f"$p=Start-Process -FilePath '{executable}' "
            "-ArgumentList '-c','\"import time;time.sleep(6)\"' -PassThru -WindowStyle Hidden\n"
            "$p.Id | Set-Content (Join-Path $PSScriptRoot 'child.pid')\n", encoding="utf-8-sig")
        started = time.monotonic()
        try:
            with patch("urllib.request.OpenerDirector.open", side_effect=OSError("offline")):
                self.assertEqual(ensure_running(self.root, 18769), 0)
            self.assertLess(time.monotonic() - started, 5,
                            "恢复检查不应等待常驻后台子进程退出")
        finally:
            pid_file = scripts / "child.pid"
            if pid_file.exists():
                # 仅等待本次合成子进程自然退出，不结束任何系统或业务进程。
                child_pid = int(pid_file.read_text("utf-8-sig").strip())
                powershell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
                subprocess.run([powershell, "-NoProfile", "-Command",
                                f"Wait-Process -Id {child_pid} -Timeout 10 -ErrorAction SilentlyContinue"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)


if __name__ == "__main__":
    unittest.main()
