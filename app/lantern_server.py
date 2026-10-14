"""彩灯部门独立内网服务；共享安全规则，业务路由与生产看板完全分离。"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import threading
import time
import uuid
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs

from app import __version__
from app.lantern_store import LanternStore, MAX_UPLOAD_BYTES, RevisionConflict
from app.server import Handler as SafeHandler

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "lantern_session"


class LanternServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, lan_ip, *, workspace_id=None, web_root=None):
        self.store = store
        from app.lantern_jobs import LanternJobs
        self.jobs = LanternJobs(store.workspace)
        self.lan_ip = lan_ip
        self.workspace_id = workspace_id or hashlib.sha256(str(store.workspace.resolve()).casefold().encode("utf-8")).hexdigest()
        self.web_root = Path(web_root) if web_root is not None else ROOT / "web" / "lantern"
        self.network = ipaddress.ip_network(lan_ip + "/24", strict=False)
        self.attempts = {}
        self.attempt_lock = threading.Lock()
        super().__init__(address, LanternHandler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}", f"{lan_ip}:{self.server_port}"}


class LanternHandler(SafeHandler):
    server_version = "LanternDashboard/" + __version__

    def identity(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            user = self.server.store.authenticate(auth[7:], api=True)
            return user if user and user["role"] == "admin" else None
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        cookie = cookies.get(COOKIE)
        self.session_token = cookie.value if cookie else ""
        return self.server.store.authenticate(self.session_token) if cookie else None

    def body(self):
        if urlparse(self.path).path not in {"/api/lantern/orders/upload", "/api/lantern/documents/extract"}:
            return super().body()
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("请求需使用JSON格式")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 8192:
            raise ValueError("原生产单附件不能超过100MB")
        result = json.loads(self.rfile.read(size))
        if not isinstance(result, dict):
            raise ValueError("请求需为JSON对象")
        return result

    def download(self, body, filename, *, content_type="application/octet-stream"):
        self.send_response(200)
        self.common_headers(content_type)
        self.send_header("Content-Disposition", 'attachment; filename="lantern-download.'
                         + Path(filename).suffix.lstrip(".") + '"; filename*=UTF-8\'\'' + quote(filename, safe=""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        return self.wfile.write(body)

    def do_GET(self):
        if not self.access():
            return self.json({"error": "仅允许本机及已配置的公司内网访问"}, 403)
        path = urlparse(self.path).path
        if path == "/health":
            return self.json({"status": "ready", "department": "lantern", "version": __version__, "workspace_id": self.server.workspace_id})
        if path in {"/", "/index.html", "/app.js", "/styles.css"}:
            filename = "index.html" if path == "/" else path[1:]
            target = self.server.web_root / filename
            if not target.is_file():
                return self.json({"error": "彩灯页面文件未就绪"}, 503)
            content = target.read_bytes()
            self.send_response(200)
            self.common_headers({"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8"}[filename])
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            return self.wfile.write(content)
        user = self.require(allow_password_change=path == "/api/me")
        if not user:
            return
        try:
            if path == "/api/me":
                return self.json({"user": {k: v for k, v in user.items() if k not in {"csrf", "auth_type"}},
                                  "csrf": user["csrf"], "version": __version__, "department": "lantern",
                                  "lan_url": f"http://{self.server.lan_ip}:{self.server.server_port}"})
            if path == "/api/lantern":
                return self.json(self.server.store.snapshot())
            if path == "/api/jobs/current":
                return self.json(self.server.jobs.status())
            if path == "/api/lantern/inbox" or path.startswith("/api/lantern/inbox/"):
                if user["auth_type"] != "session":
                    return self.json({"error": "请在网页登录后核对邮箱附件"}, 403)
                from app.lantern_inbox import EmailInbox
                inbox = EmailInbox(self.server.store)
                if path == "/api/lantern/inbox":
                    query = parse_qs(urlparse(self.path).query)
                    return self.json(inbox.listing(int(query.get("offset", ["0"])[0])))
                document_id = path[len("/api/lantern/inbox/"):]
                if document_id.endswith("/confirmed"):
                    record = inbox.get(document_id[:-len("/confirmed")])
                    if record["status"] != "confirmed":
                        return self.json({"error": "此附件尚未核对，不能带入报关资料"}, 403)
                    return self.json(record)
                if document_id.endswith("/original"):
                    record, content = inbox.original(document_id[:-len("/original")])
                    return self.download(content, record["filename"])
                return self.json(inbox.get(document_id))
            if path == "/api/lantern/settings/email":
                if user["role"] != "admin" or user["auth_type"] != "session":
                    return self.json({"error": "请使用管理员网页登录邮箱设置"}, 403)
                from app.lantern_email_settings import settings
                return self.json(settings(self.server.store.workspace))
            if path == "/api/lantern/documents/templates":
                from app.lantern_documents import templates
                return self.json({"templates": templates(self.server.store.workspace)})
            if path == "/api/users":
                if user["role"] != "admin":
                    return self.json({"error": "仅江红可以管理授权账号"}, 403)
                return self.json({"users": self.server.store.users()})
            if path == "/api/tokens":
                if user["auth_type"] != "session" or user["role"] != "admin":
                    return self.json({"error": "个人只读令牌仅供管理员管理"}, 403)
                return self.json({"tokens": self.server.store.api_tokens(user["username"])})
            exports = {f"/api/lantern/{kind}.{fmt}": (kind, fmt) for kind in ("projects", "shipments") for fmt in ("csv", "xlsx")}
            if path in exports:
                kind, fmt = exports[path]
                body = self.server.store.export(kind, xlsx=fmt == "xlsx")
                filename = f"彩灯{'项目产品进度' if kind == 'projects' else '出货跟踪'}_V{__version__}.{fmt}"
                return self.download(body, filename, content_type="text/csv; charset=utf-8" if fmt == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            if path in {"/materials-checklist.txt", "/api/lantern/materials-checklist.txt"}:
                target = ROOT / "docs" / "lantern-materials-checklist.txt"
                if not target.is_file():
                    return self.json({"error": "待提供材料清单文件尚未就绪"}, 503)
                return self.download(target.read_bytes(), "彩灯看板待提供材料清单.txt", content_type="text/plain; charset=utf-8")
            if path.startswith("/api/lantern/orders/"):
                attachment, content = self.server.store.order_attachment(unquote(path[len("/api/lantern/orders/"):]))
                return self.download(content, attachment["filename"])
            return self.json({"error": "接口不存在"}, 404)
        except KeyError as exc:
            return self.json({"error": str(exc.args[0])}, 404)
        except (ValueError, TypeError) as exc:
            return self.json({"error": str(exc)}, 400)
        except Exception:
            return self.json({"error": "服务暂时处理失败；原有彩灯数据已保留"}, 500)

    @staticmethod
    def fields(data, allowed):
        if set(data) - set(allowed):
            raise ValueError("请求包含不支持的字段")

    def mutate(self, method):
        if not self.access() or not self.valid_origin():
            return self.json({"error": "请求来源不被允许"}, 403)
        path = urlparse(self.path).path
        try:
            upload_user = None
            if path in {"/api/lantern/orders/upload", "/api/lantern/documents/extract"}:
                upload_user = self.require(write=True)
                if not upload_user:
                    return
                if upload_user["role"] not in {"admin", "editor"}:
                    return self.json({"error": "该账号仅有查看权限"}, 403)
            data = self.body()
            if path == "/api/login" and method == "POST":
                self.fields(data, {"username", "password"})
                username, password = data.get("username", ""), data.get("password", "")
                if not isinstance(username, str) or not isinstance(password, str) or len(username) > 32 or len(password) > 120:
                    raise ValueError("账号或密码格式无效")
                ip = self.client_address[0]
                with self.server.attempt_lock:
                    attempts = [t for t in self.server.attempts.get(ip, []) if t > time.monotonic() - 60]
                    if len(attempts) >= 12:
                        return self.json({"error": "尝试过多，请一分钟后再试"}, 429)
                    self.server.attempts[ip] = attempts + [time.monotonic()]
                result = self.server.store.login(username, password)
                if not result:
                    return self.json({"error": "账号或密码不正确"}, 401)
                with self.server.attempt_lock:
                    self.server.attempts.pop(ip, None)
                token = result.pop("token")
                return self.json(result, headers={"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"})
            user = upload_user or self.require(write=True, allow_password_change=method == "POST" and path in {"/api/password", "/api/logout"})
            if not user:
                return
            if path.startswith("/api/lantern/inbox/") and method == "POST" and path.endswith("/confirm"):
                if user["role"] not in {"admin", "editor"}:
                    return self.json({"error": "该账号只能查看附件"}, 403)
                from app.lantern_inbox import EmailInbox
                return self.json(EmailInbox(self.server.store).confirm(path[len("/api/lantern/inbox/"):-len("/confirm")], data, user))
            if path in {"/api/lantern/settings/email", "/api/lantern/settings/email/test"} and method == "POST":
                if user["role"] != "admin" or not ipaddress.ip_address(self.client_address[0]).is_loopback:
                    return self.json({"error": "请在部署电脑上使用管理员账号设置邮箱"}, 403)
                if self.server.jobs.status().get("status") == "running":
                    return self.json({"error": "集中整理正在运行，请完成后再修改或测试邮箱设置"}, 409)
                from app.lantern_email_settings import save, test_connection
                action = test_connection if path.endswith("/test") else save
                return self.json(action(self.server.store.workspace, data))
            if path == "/api/jobs" and method == "POST":
                if user["role"] != "admin":
                    return self.json({"error": "仅江红管理员可启动集中整理"}, 403)
                self.fields(data, {"scope"})
                scope = data.get("scope", "all")
                if not isinstance(scope, str) or scope not in {"all", "email"}:
                    raise ValueError("整理范围只能为全部已配置来源或邮箱")
                return self.json(self.server.jobs.start(user["username"], scope=scope), 202)
            if path == "/api/logout" and method == "POST":
                self.fields(data, set())
                self.server.store.logout(self.session_token)
                return self.json({"ok": True}, headers={"Set-Cookie": f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            if path == "/api/password" and method == "POST":
                self.fields(data, {"old_password", "new_password"})
                old, new = data.get("old_password", ""), data.get("new_password", "")
                if not isinstance(old, str) or not isinstance(new, str):
                    raise ValueError("密码格式无效")
                self.server.store.change_password(user["username"], old, new)
                return self.json({"ok": True}, headers={"Set-Cookie": f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            if path == "/api/users" and method == "POST":
                if user["role"] != "admin":
                    return self.json({"error": "仅江红可以新增彩灯授权账号"}, 403)
                self.fields(data, {"username", "display_name", "role"})
                if any(not isinstance(v, str) for v in data.values()):
                    raise ValueError("账号字段需为文字")
                return self.json(self.server.store.create_user(data.get("username", ""), data.get("display_name", ""), data.get("role", "viewer")), 201)
            if path.startswith("/api/users/"):
                if user["role"] != "admin":
                    return self.json({"error": "仅江红可以管理彩灯授权账号"}, 403)
                username = unquote(path[len("/api/users/"):])
                if username.endswith("/reset-password") and method == "POST":
                    self.fields(data, {"reset"})
                    return self.json(self.server.store.reset_user_password(username[:-len("/reset-password")]))
                if method == "PATCH" and "/" not in username:
                    self.server.store.manage_user(username, data)
                    return self.json({"ok": True})
            if path == "/api/tokens" and method == "POST":
                if user["role"] != "admin":
                    return self.json({"error": "个人只读令牌仅供管理员管理"}, 403)
                self.fields(data, {"label", "days"})
                label, days = data.get("label", ""), data.get("days", 30)
                if not isinstance(label, str) or type(days) is not int:
                    raise ValueError("令牌用途需为文字，有效期需为7、30或90天")
                return self.json(self.server.store.create_api_token(user["username"], label, days), 201)
            if path.startswith("/api/tokens/") and method == "PATCH":
                if user["role"] != "admin":
                    return self.json({"error": "个人只读令牌仅供管理员管理"}, 403)
                self.fields(data, {"revoked"})
                if data.get("revoked") is not True:
                    raise ValueError("吊销令牌需提供revoked=true")
                self.server.store.revoke_api_token(user["username"], unquote(path[len("/api/tokens/"):]))
                return self.json({"ok": True})
            if path.startswith("/api/lantern/"):
                if user["role"] not in {"admin", "editor"}:
                    return self.json({"error": "该账号仅有查看权限"}, 403)
                if path == "/api/lantern/documents/extract" and method == "POST":
                    self.fields(data, {"kind", "filename", "content_base64"})
                    if data.get("kind") not in {"pi", "packing"}:
                        raise ValueError("提取类型需为PI或装箱单")
                    if not isinstance(data.get("content_base64"), str) or not isinstance(data.get("filename"), str):
                        raise ValueError("请提供有效的文件名和内容")
                    try:
                        content = base64.b64decode(data["content_base64"], validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError("文件内容编码无效") from exc
                    if not 0 < len(content) <= MAX_UPLOAD_BYTES:
                        raise ValueError("文件不能为空或超过100MB")
                    from app.lantern_documents import read_rows, extract_pi, extract_packing
                    rows = read_rows(data["filename"], content)
                    result = extract_pi(rows) if data["kind"] == "pi" else extract_packing(rows)
                    attachment = self.server.store.upload_order(data["filename"], content, user)
                    result["attachment_id"] = attachment["id"]
                    return self.json(result)
                if path == "/api/lantern/documents/generate" and method == "POST":
                    self.fields(data, {"template_id", "fields", "source_ids", "inbox_sources", "confirmed"})
                    if data.get("confirmed") is not True:
                        raise ValueError("请先核对PI信息和装箱合计")
                    source_ids = data.get("source_ids", [])
                    if not isinstance(source_ids, list) or len(source_ids) > 10 or any(not isinstance(x, str) for x in source_ids):
                        raise ValueError("资料来源编号无效")
                    for source_id in source_ids:
                        self.server.store.order_attachment(source_id)
                    inbox_sources = data.get("inbox_sources", [])
                    if not isinstance(inbox_sources, list) or len(inbox_sources) > 2:
                        raise ValueError("邮箱附件核对来源无效")
                    from app.lantern_inbox import EmailInbox
                    for document_id in inbox_sources:
                        record = EmailInbox(self.server.store).get(document_id)
                        if record["status"] != "confirmed" or record["kind"] not in {"pi", "packing"}:
                            raise ValueError("邮箱附件尚未核对，不能生成报关资料")
                    from app.lantern_documents import generate
                    body = generate(self.server.store.workspace, data.get("template_id"), data.get("fields"))
                    output_id = uuid.uuid4().hex
                    target = self.server.store.private / "lantern_documents" / (output_id + ".xlsx")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    from app.store import now
                    with self.server.store.connect() as conn:
                        conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                                     (now(), user["username"], "lantern_customs_draft", output_id,
                                      json.dumps({"template_id": data["template_id"], "source_ids": source_ids, "inbox_sources": inbox_sources,
                                                  "sha256": hashlib.sha256(body).hexdigest(), "confirmed": True})))
                    return self.download(body, "报关资料_待核对_" + output_id[:8] + ".xlsx",
                                         content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                if path == "/api/lantern/orders/upload" and method == "POST":
                    self.fields(data, {"filename", "content_base64"})
                    encoded = data.get("content_base64")
                    if not isinstance(encoded, str):
                        raise ValueError("附件需提供有效的base64内容")
                    try:
                        content = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError("附件base64内容无效") from exc
                    return self.json({"attachment": self.server.store.upload_order(data.get("filename"), content, user)}, 201)
                for kind, singular, saver in (("projects", "project", self.server.store.save_project), ("shipments", "shipment", self.server.store.save_shipment)):
                    endpoint = "/api/lantern/" + kind
                    if path == endpoint and method == "POST":
                        return self.json({singular: saver(data, user)}, 201)
                    if path.startswith(endpoint + "/") and method == "PATCH":
                        return self.json({singular: saver(data, user, unquote(path[len(endpoint) + 1:]))})
            return self.json({"error": "接口不存在"}, 404)
        except RevisionConflict as exc:
            return self.json({"error": str(exc), "code": "revision_conflict"}, 409)
        except PermissionError as exc:
            return self.json({"error": str(exc)}, 403)
        except KeyError as exc:
            return self.json({"error": str(exc.args[0])}, 404)
        except (ValueError, TypeError) as exc:
            return self.json({"error": str(exc)}, 400)
        except Exception:
            return self.json({"error": "服务暂时处理失败；原有彩灯数据已保留"}, 500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--lan-ip", default="127.0.0.1")
    parser.add_argument("--workspace-id", default=None)
    args = parser.parse_args()
    store = LanternStore(ROOT)
    server = LanternServer((args.host, args.port), store, args.lan_ip, workspace_id=args.workspace_id)
    print(f"彩灯部门看板 V{__version__} http://{args.lan_ip}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
