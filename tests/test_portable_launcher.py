"""Windows便携启动器的隔离验证；不访问正式服务和业务数据库。"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "仅Windows PowerShell环境执行")
class PortableLauncherTests(unittest.TestCase):
    def run_script(self, root, *arguments):
        command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File",
                   str(root / "scripts" / "start_dashboard_V0.2.0.ps1"), "-NoBrowser",
                   "-PythonPath", sys.executable, *arguments]
        # Windows后台子进程可能继承匿名管道句柄，用文件收集测试输出避免等EOF。
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
            result = subprocess.run(command, stdout=output, stderr=error, timeout=40)
            output.seek(0)
            error.seek(0)
            return subprocess.CompletedProcess(command, result.returncode, output.read(), error.read())

    def copy_app(self, root):
        for name in ("app", "web", "scripts"):
            shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "VERSION", root / "VERSION")

    def test_start_and_reuse_in_directory_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="dashboard launcher ") as folder:
            root = Path(folder).resolve()
            self.copy_app(root)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            pid_file = root / "private" / f"server_port_{port}.pid"
            process_id = None
            workspace_id = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()
            try:
                result = self.run_script(root, "-Port", str(port), "-LanAddress", "127.0.0.1")
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                process_id = int(pid_file.read_text(encoding="utf-8-sig").strip())
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    health = json.load(response)
                self.assertEqual(health["workspace_id"], workspace_id)
                self.assertEqual(health["version"], (root / "VERSION").read_text().strip())
                again = self.run_script(root, "-Port", str(port), "-LanAddress", "127.0.0.1")
                self.assertEqual(again.returncode, 0, again.stderr.decode(errors="replace"))
                self.assertEqual(int(pid_file.read_text(encoding="utf-8-sig").strip()), process_id)
            finally:
                if process_id is None and pid_file.is_file():
                    process_id = int(pid_file.read_text(encoding="utf-8-sig").strip())
                if process_id:
                    # 只允许结束此测试工作区身份、端口和命令均匹配的测试服务。
                    script = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {process_id}'; "
                              f"if ($p.CommandLine -match 'app\\.server' -and "
                              f"$p.CommandLine -match '--port {port}(?:\\s|$)' -and "
                              f"$p.CommandLine -match '--workspace-id {workspace_id}(?:\\s|$)') "
                              f"{{ Stop-Process -Id {process_id} -Force }} else {{ exit 2 }}")
                    stopped = subprocess.run([POWERSHELL, "-NoProfile", "-Command", script], capture_output=True, timeout=10)
                    self.assertEqual(stopped.returncode, 0, "隔离测试服务身份核验失败，未停止其他进程")

    def test_unrelated_listener_does_not_replace_pid_file(self):
        class FakeHealth(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ready","version":"0.2.6"}')

            def log_message(self, *_):
                pass

        with tempfile.TemporaryDirectory(prefix="dashboard occupied ") as folder:
            root = Path(folder)
            self.copy_app(root)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHealth)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port
            pid_file = root / "private" / f"server_port_{port}.pid"
            pid_file.parent.mkdir()
            pid_file.write_text("123456789\n", encoding="utf-8")
            original = pid_file.read_bytes()
            try:
                result = self.run_script(root, "-Port", str(port), "-LanAddress", "127.0.0.1")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(pid_file.read_bytes(), original)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_scripts_use_utf8_bom_for_windows_powershell(self):
        for name in ("start_dashboard_V0.2.0.ps1", "check_environment.ps1"):
            with self.subTest(script=name):
                self.assertTrue((ROOT / "scripts" / name).read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
