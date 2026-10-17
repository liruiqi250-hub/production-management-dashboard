"""由随包 pythonw.exe 运行的无控制台登录自启及周期健康检查。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def ensure_running(root: Path = ROOT, port: int = 8769) -> int:
    """健康时只请求本地服务；不可达时调用带身份核验的原启动器。"""
    root = root.resolve()
    version = (root / "VERSION").read_text(encoding="utf-8-sig").strip()
    workspace_id = hashlib.sha256(str(root).lower().encode("utf-8")).hexdigest()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            health = json.loads(response.read(16384))
        if (isinstance(health, dict) and health.get("status") == "ready"
                and health.get("version") == version and health.get("workspace_id") == workspace_id):
            return 0
        _record(root, "健康接口返回其他版本或工作区；现有服务未改动。")
        return 2
    except (OSError, ValueError, urllib.error.URLError):
        pass
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        # Windows孙进程可能继承匿名管道，导致communicate一直等服务退出。
        # 使用临时文件接收输出，启动器退出后巡检即可退出，不常驻等待服务。
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "RemoteSigned", "-File",
                 str(root / "scripts/start_dashboard_V0.2.0.ps1"), "-NoBrowser", "-Port", str(port)],
                cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=90,
            )
            if result.returncode:
                output.seek(0, 2)
                output.seek(max(0, output.tell() - 6000))
                _record(root, f"启动器退出码 {result.returncode}\n" +
                        output.read().decode("utf-8", errors="replace"))
        return result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record(root, f"后台恢复失败：{type(exc).__name__}: {exc}")
        return 1


def _record(root: Path, message: str) -> None:
    folder = root / "private"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "startup_last_error.log").write_text(
        datetime.now().isoformat(timespec="seconds") + "\n" + message + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8769, choices=range(1, 65536), metavar="PORT")
    arguments = parser.parse_args()
    raise SystemExit(ensure_running(port=arguments.port))
