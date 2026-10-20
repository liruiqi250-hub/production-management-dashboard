"""生产 PDF 的显式打印队列；提交给 Windows 不等于已经出纸。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import uuid

from app.store import now

PRINTER_NAME = "KONICA MINOLTA bizhub C226 PCL (192.168.10.199) UPD"
ACTIVE_STATES = {"queued", "submitting"}
TERMINAL_STATES = {"submitted", "failed", "unknown"}
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_SHEETS = 250
SUBMITTED_MESSAGE = "已交给 Windows 打印队列，不代表已经出纸。"


def parse_pages(value, total_pages):
    """空字符串为全部页；支持 1-3,5，返回升序且去重的物理页码。"""
    if type(total_pages) is not int or not 1 <= total_pages <= 10000:
        raise ValueError("PDF 页数无效。")
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError("页码请填写 1-3,5，留空表示全部。")
    if not value.strip():
        return list(range(1, total_pages + 1))
    pages = set()
    for part in re.split(r"[,，;；]", value.strip()):
        match = re.fullmatch(r"\s*([1-9]\d*)(?:\s*-\s*([1-9]\d*))?\s*", part)
        if not match:
            raise ValueError("页码格式无效，请填写 1-3,5。")
        first, last = int(match[1]), int(match[2] or match[1])
        if first > last or last > total_pages:
            raise ValueError("页码超出 PDF 范围或起止顺序有误。")
        pages.update(range(first, last + 1))
    return sorted(pages)


def _sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _inside(root, relative):
    root = Path(root)
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("打印目录不能使用链接。")
    root = root.resolve()
    parts = relative.split("/")
    if any(not part or part in {".", ".."} or ":" in part or "\\" in part for part in parts):
        raise ValueError("打印文件路径无效。")
    target = root.joinpath(*parts)
    cursor = target
    while cursor != root:
        if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
            raise ValueError("打印文件不能使用链接。")
        cursor = cursor.parent
    if not target.resolve().is_relative_to(root):
        raise ValueError("打印文件超出私有范围。")
    return target


class ProductionPrinting:
    def __init__(self, store, documents, *, runner=None):
        self.store, self.documents = store, documents
        self.root = Path(store.workspace).resolve()
        self.base = _inside(self.root, "private/production_print_jobs")
        self.config_path = _inside(self.root, "private/production_printing.json")
        self._runner = runner
        self._guard = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._process = None
        self._office_process = None
        with store.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS production_print_jobs(
                id TEXT PRIMARY KEY,username TEXT NOT NULL,request_id TEXT NOT NULL,
                output_id TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,data_json TEXT NOT NULL,UNIQUE(username,request_id))""")

    def _config(self):
        if not self.config_path.is_file():
            return {"schema": 1, "enabled": False, "printer_name": PRINTER_NAME}
        data = json.loads(self.config_path.read_text("utf-8-sig"))
        if (not isinstance(data, dict) or data.get("schema") != 1
                or type(data.get("enabled")) is not bool or data.get("printer_name") != PRINTER_NAME
                or set(data) - {"schema", "enabled", "printer_name"}):
            raise ValueError("本机打印配置无效，请由管理员检查。")
        return data

    def _tools(self):
        from app.production_documents import find_libreoffice
        office = find_libreoffice()
        python = office.parent / "python.exe" if office else None
        helper = _inside(self.root, "scripts/print_production_pdf.py")
        if not office or not python.is_file() or not helper.is_file():
            raise ValueError("本机缺少 LibreOffice 打印组件。")
        return python, helper

    def _printer(self):
        # 只读枚举固定目标；命令不含网页输入，也不会创建打印作业。
        command = ("$ErrorActionPreference='Stop';"
                   "$p=Get-Printer -Name 'KONICA MINOLTA bizhub C226 PCL (192.168.10.199) UPD';"
                   "$q=Get-PrinterPort -Name $p.PortName;"
                   "[pscustomobject]@{name=$p.Name;status=[string]$p.PrinterStatus;"
                   "port=$p.PortName;host_address=$q.PrinterHostAddress}|ConvertTo-Json -Compress")
        process = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if process.returncode:
            raise ValueError("当前 Windows 账号未找到已配置的办公室打印机。")
        data = json.loads(process.stdout.decode("utf-8-sig", errors="replace"))
        if data.get("name") != PRINTER_NAME:
            raise ValueError("本机打印机不在允许范围。")
        return data

    def status(self):
        result = {"enabled": False, "printer_name": PRINTER_NAME, "available": False,
                  "reason": "尚未启用办公室打印。", "copies_max": 5, "total_sheets_max": MAX_SHEETS,
                  "notice": SUBMITTED_MESSAGE}
        try:
            config = self._config()
            result["enabled"] = config["enabled"]
            if not config["enabled"]:
                return result
            self._tools()
            printer = self._printer()
            result.update(printer_status=printer.get("status", ""), port_name=printer.get("port", ""),
                          host_address=printer.get("host_address", ""))
            if any(word in str(printer.get("status", "")).casefold() for word in ("offline", "error", "notavailable")):
                result["reason"] = "Windows 打印队列当前不可用，请检查打印机。"
            else:
                result.update(available=True, reason="")
        except (OSError, ValueError, subprocess.SubprocessError):
            result["reason"] = "本机打印组件或打印队列暂不可用，请检查配置。"
        return result

    def _actor(self, actor):
        if not isinstance(actor, dict) or actor.get("auth_type") != "session" or not isinstance(actor.get("username"), str):
            raise PermissionError("请在网页登录后提交或查看本人打印任务。")
        with self.store.connect() as conn:
            row = conn.execute("SELECT username,role,active,must_change_password FROM users WHERE username=?", (actor["username"],)).fetchone()
        if not row or not row["active"] or row["must_change_password"]:
            raise PermissionError("账号已停用或需要先修改初始密码。")
        return dict(row)

    @staticmethod
    def _public(job):
        keys = ("id", "output_id", "state", "message", "pages", "copies", "total_sheets", "printer_name", "created_at", "updated_at")
        return {key: job[key] for key in keys}

    def _get(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise KeyError("打印任务不存在。")
        with self.store.connect() as conn:
            row = conn.execute("SELECT data_json FROM production_print_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("打印任务不存在。")
        return json.loads(row["data_json"])

    def get(self, job_id, actor):
        user = self._actor(actor)
        job = self._get(job_id)
        if user["username"] != job["username"] and user["role"] != "admin":
            raise PermissionError("只能查看自己的打印任务。")
        return self._public(job)

    def active_output_ids(self):
        with self.store.connect() as conn:
            return {row[0] for row in conn.execute("SELECT DISTINCT output_id FROM production_print_jobs WHERE state IN ('queued','submitting')")}

    def _existing(self, username, request_id):
        with self.store.connect() as conn:
            row = conn.execute("SELECT data_json FROM production_print_jobs WHERE username=? AND request_id=?", (username, request_id)).fetchone()
        return json.loads(row["data_json"]) if row else None

    def _same_request(self, existing, output_id, pages, copies):
        if existing["output_id"] != output_id or existing["copies"] != copies or existing["pages"] != parse_pages(pages, existing["document_pages"]):
            raise ValueError("此请求编号已用于另一组打印参数，请重新点击打印。")
        return self._public(existing)

    def submit(self, output_id, actor, pages="", copies=1, request_id=None):
        user = self._actor(actor)
        if not isinstance(output_id, str) or not re.fullmatch(r"[a-f0-9]{32}", output_id):
            raise ValueError("生成资料编号无效。")
        if type(copies) is not int or not 1 <= copies <= 5:
            raise ValueError("打印份数必须为 1 至 5 的整数。")
        if not isinstance(request_id, str) or len(request_id) > 64:
            raise ValueError("打印请求需要唯一编号，请重新点击打印。")
        try:
            request_id = str(uuid.UUID(request_id))
        except ValueError as exc:
            raise ValueError("打印请求编号无效。") from exc
        with self._guard:
            existing = self._existing(user["username"], request_id)
            if existing:
                return self._same_request(existing, output_id, pages, copies)
            status = self.status()
            if not status["enabled"] or not status["available"]:
                raise ValueError(status["reason"] or "办公室打印暂不可用。")
            with self.documents.hold(output_id):
                source, filename, mime = self.documents.file(output_id, "pdf")
                if mime != "application/pdf" or not 0 < source.stat().st_size <= MAX_PDF_BYTES:
                    raise ValueError("打印原件须为 100MB 以内的 PDF。")
                from pypdf import PdfReader
                count = len(PdfReader(str(source)).pages)
                selected = parse_pages(pages, count)
                if len(selected) * copies > MAX_SHEETS:
                    raise ValueError("本次页数乘份数超过 250，请缩小范围。")
                job_id, timestamp = uuid.uuid4().hex, now()
                folder = _inside(self.base, job_id)
                folder.mkdir(parents=True, exist_ok=False)
                payload = _inside(self.base, job_id + "/payload.pdf")
                identity = None
                try:
                    source_sha = _sha(source)
                    with source.open("rb") as origin, payload.open("xb") as destination:
                        stat = os.fstat(destination.fileno())
                        identity = (stat.st_dev, stat.st_ino)
                        shutil.copyfileobj(origin, destination, length=1024 * 1024)
                    if _sha(payload) != source_sha:
                        raise ValueError("打印副本校验失败，未加入队列。")
                except Exception as exc:
                    self._remove_unqueued_payload(job_id, identity)
                    raise ValueError("打印副本准备失败，本次未加入队列。") from exc
            job = {"id": job_id, "output_id": output_id, "username": user["username"], "request_id": request_id,
                   "state": "queued", "message": "已加入本机打印队列，等待提交。", "pages": selected,
                   "copies": copies, "total_sheets": len(selected) * copies, "document_pages": count,
                   "printer_name": PRINTER_NAME, "created_at": timestamp, "updated_at": timestamp,
                   "payload_sha256": source_sha}
            manifest = {"schema": 1, "job_id": job_id, "output_id": output_id, "payload": "payload.pdf",
                        "payload_sha256": source_sha, "printer_name": PRINTER_NAME, "pages": selected,
                        "copies": copies, "document_pages": count}
            try:
                _inside(self.base, job_id + "/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                self._remove_unqueued_payload(job_id, identity)
                raise ValueError("打印清单准备失败，本次未加入队列。") from exc
            try:
                with self.store.connect() as conn:
                    conn.execute("INSERT INTO production_print_jobs VALUES(?,?,?,?,?,?,?,?)",
                                 (job_id, user["username"], request_id, output_id, "queued", timestamp, timestamp, json.dumps(job, ensure_ascii=False)))
                    self._audit(conn, job, "queue_production_print")
            except sqlite3.IntegrityError:
                self._clean_payload(job)
                existing = self._existing(user["username"], request_id)
                if not existing:
                    raise
                return self._same_request(existing, output_id, pages, copies)
            self._wake.set()
            return self._public(job)

    @staticmethod
    def _audit(conn, job, action):
        conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                     (now(), job["username"], action, job["output_id"], json.dumps(ProductionPrinting._public(job), ensure_ascii=False)))

    def _clean_payload(self, job):
        # 只移除该任务经过摘要核对的 PDF，不递归删目录、不删来源或清单。
        try:
            path = _inside(self.base, job["id"] + "/payload.pdf")
            if path.is_file() and _sha(path) == job["payload_sha256"]:
                path.unlink()
        except (OSError, ValueError):
            pass

    def _remove_unqueued_payload(self, job_id, identity):
        """准备失败时只删除当次 xb 新建的那个文件，不能按未登记的内容猜测目标。"""
        if identity is None or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            return
        try:
            path = _inside(self.base, job_id + "/payload.pdf")
            current = path.stat()
            if (current.st_dev, current.st_ino) == identity and path.is_file():
                path.unlink()
        except (OSError, ValueError):
            pass

    def _finish(self, job, state, message):
        job.update(state=state, message=message, updated_at=now())
        with self.store.connect() as conn:
            conn.execute("UPDATE production_print_jobs SET state=?,updated_at=?,data_json=? WHERE id=?",
                         (state, job["updated_at"], json.dumps(job, ensure_ascii=False), job["id"]))
            self._audit(conn, job, "production_print_result")
        self._clean_payload(job)

    def _recover(self):
        with self.store.connect() as conn:
            rows = conn.execute("SELECT data_json FROM production_print_jobs WHERE state='submitting'").fetchall()
        for row in rows:
            self._finish(json.loads(row[0]), "unknown", "上次提交过程中服务中断，结果未知；未自动重印，请先查看打印队列。")
        # 终态已落库、但进程尚未来得及删副本就退出的情况，只补做已校验 PDF 的清理。
        with self.store.connect() as conn:
            terminal = conn.execute("SELECT data_json FROM production_print_jobs WHERE state IN ('submitted','failed','unknown')").fetchall()
        for row in terminal:
            self._clean_payload(json.loads(row[0]))

    @contextmanager
    def _worker_lock(self):
        from app.jobs import file_lock
        with file_lock(_inside(self.base, "worker.lock")):
            yield

    def _take(self):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT data_json FROM production_print_jobs WHERE state='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                return None
            job = json.loads(row[0])
            job.update(state="submitting", message="正在交给 Windows 打印队列，请勿重复点击。", updated_at=now())
            conn.execute("UPDATE production_print_jobs SET state='submitting',updated_at=?,data_json=? WHERE id=?",
                         (job["updated_at"], json.dumps(job, ensure_ascii=False), job["id"]))
            return job

    def _execute(self, job):
        try:
            self._actor({"username": job["username"], "auth_type": "session"})
            status = self.status()
            if not status["enabled"] or not status["available"]:
                return self._finish(job, "failed", "提交前打印组件或队列已不可用，本次未提交。")
            payload = _inside(self.base, job["id"] + "/payload.pdf")
            if not payload.is_file() or _sha(payload) != job["payload_sha256"]:
                return self._finish(job, "failed", "打印副本缺失或校验失败，本次未提交。")
        except (PermissionError, ValueError, OSError):
            return self._finish(job, "failed", "账号或打印副本校验失败，本次未提交。")
        try:
            runner = self._run_helper if self._runner is None else self._runner
            result = runner(_inside(self.base, job["id"] + "/manifest.json"))
            state = result.get("state") if isinstance(result, dict) else None
            if state == "submitted":
                self._finish(job, "submitted", SUBMITTED_MESSAGE)
            elif state == "failed":
                self._finish(job, "failed", "本机打印组件拒绝了本次提交，请检查设备或打印参数。")
            else:
                self._finish(job, "unknown", "提交结果未知，请先查看 Windows 打印队列，勿直接重复打印。")
        except Exception:
            self._finish(job, "unknown", "提交中断，结果未知；请先查看 Windows 打印队列，未自动重印。")

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            process.kill()

    def _run_helper(self, manifest, *, dry_run=False):
        python, helper = self._tools()
        office = python.parent / "soffice.com"
        if not office.is_file():
            office = python.parent / "soffice.exe"
        manifest = Path(manifest)
        if not manifest.parent.resolve().is_relative_to(self.base.resolve()) or not re.fullmatch(r"[a-f0-9]{32}", manifest.parent.name):
            raise ValueError("打印清单路径无效。")
        # Windows 版 LO 在过深的全新 profile 路径下可能原生退出；使用根目录下的短私有临时路径。
        profile_base = _inside(self.root, "private/lo_print_profiles")
        profile_base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="p_", dir=profile_base)
        profile = Path(temporary.name)
        if profile.is_symlink() or not profile.resolve().is_relative_to(profile_base.resolve()):
            raise ValueError("临时打印配置目录无效。")
        pipe = "production_print_" + uuid.uuid4().hex
        process, office_process = None, None
        try:
            environment = dict(os.environ)
            for name in ("PYTHONHOME", "PYTHONPATH", "UNO_PATH", "URE_BOOTSTRAP"):
                environment.pop(name, None)
            # 从看板解释器启动 soffice；LO 的 Python 仅连接 UNO，避免其启动器环境影响原生子进程。
            office_process = subprocess.Popen([str(office), "-env:UserInstallation=" + profile.resolve().as_uri(),
                                               "--headless", "--invisible", "--nologo", "--norestore", "--nodefault",
                                               "--accept=pipe,name=" + pipe + ";urp;StarOffice.ServiceManager"],
                                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            command = [str(python), str(helper), "--root", str(self.root), "--manifest", str(manifest), "--pipe", pipe]
            if dry_run:
                command.append("--dry-run")
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with self._guard:
                self._process, self._office_process = process, office_process
            try:
                output, _ = process.communicate(timeout=180)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                process.communicate(timeout=10)
                return {"state": "unknown"}
            try:
                result = json.loads(output.decode("utf-8-sig").strip().splitlines()[-1])
                if dry_run and office_process.poll() is not None:
                    office_output, office_error = office_process.communicate(timeout=5)
                    result["office_exit_code"] = office_process.returncode
                    result["office_diagnostic"] = (office_output + office_error).decode("utf-8", "replace")[:500]
                allowed = TERMINAL_STATES | ({"dry_run"} if dry_run else set())
                if process.returncode and result.get("state") in {"submitted", "dry_run"}:
                    return {"state": "unknown"}
                return result if result.get("state") in allowed else {"state": "unknown"}
            except (ValueError, IndexError, UnicodeDecodeError, AttributeError):
                return {"state": "unknown"}
        finally:
            with self._guard:
                self._process, self._office_process = None, None
            if office_process:
                try:
                    office_process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    self._terminate(office_process)
                    office_process.communicate(timeout=10)
            if not profile.is_symlink() and profile.resolve().is_relative_to(profile_base.resolve()):
                try:
                    temporary.cleanup()
                except OSError:
                    pass

    def _loop(self):
        try:
            with self._worker_lock():
                self._recover()
                self._ready.set()
                while not self._stop.is_set():
                    job = self._take()
                    if job:
                        self._execute(job)
                    else:
                        self._wake.wait(1)
                        self._wake.clear()
        except (ValueError, OSError):
            self._ready.set()

    def start(self):
        with self._guard:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._ready.clear()
            self._thread = threading.Thread(target=self._loop, name="production-print-worker", daemon=True)
            self._thread.start()
        self._ready.wait(2)

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self._guard:
            processes = (self._process, self._office_process)
        for process in processes:
            if process:
                self._terminate(process)
        if self._thread:
            self._thread.join(timeout=12)
