"""内网生产看板：会话账号、只读Codex令牌、报表和静态网页。"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from app import __version__
from app.store import Store

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "dino_session"


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, lan_ip):
        self.store = store
        from app.after_sales import AfterSales
        from app.jobs import Jobs
        self.after_sales = AfterSales(store)
        self.jobs = Jobs(store.workspace)
        self.lan_ip = lan_ip
        self.network = ipaddress.ip_network(lan_ip + "/24", strict=False)
        self.attempts = {}
        self.attempt_lock = threading.Lock()
        super().__init__(address, Handler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}", f"{lan_ip}:{self.server_port}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "DinoProduction/" + __version__

    def log_message(self, *_):
        pass

    def common_headers(self, content_type):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")

    def json(self, data, status=200, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.common_headers("application/json; charset=utf-8")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def access(self):
        try:
            client = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return (client.is_loopback or client in self.server.network) and self.headers.get("Host", "") in self.server.allowed_hosts

    def identity(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return self.server.store.authenticate(auth[7:], api=True)
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        token = cookies.get(COOKIE)
        self.session_token = token.value if token else ""
        return self.server.store.authenticate(self.session_token) if token else None

    def require(self, write=False, admin=False, allow_password_change=False):
        user = self.identity()
        if not user:
            self.json({"error": "请先登录，或提供有效的个人只读令牌"}, 401)
            return None
        if user["must_change_password"] and not allow_password_change:
            self.json({"error": "请先修改本人初始密码，再进入看板", "code": "password_change_required"}, 403)
            return None
        if write:
            if user["auth_type"] != "session":
                self.json({"error": "Codex令牌仅可读取，不能修改数据"}, 403)
                return None
            if not self.valid_origin() or not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), user["csrf"]):
                self.json({"error": "请求验证失败，请重新登录"}, 403)
                return None
        if admin and user["role"] != "admin":
            self.json({"error": "只有李睿琪管理账号可以执行此操作"}, 403)
            return None
        return user

    def valid_origin(self):
        origin = self.headers.get("Origin")
        return not origin or origin in {"http://" + h for h in self.server.allowed_hosts}

    def body(self):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("请求需使用JSON格式")
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 1024 * 1024:
            raise ValueError("请求大小无效")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("请求需为JSON对象")
        return value

    def do_GET(self):
        if not self.access():
            return self.json({"error": "仅允许本机及已配置的公司内网访问"}, 403)
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            return self.json({"status": "ready", "version": __version__,
                              "workspace_id": getattr(self.server, "workspace_id", "")})
        if path in {"/", "/index.html", "/app.js", "/styles.css"}:
            name = "index.html" if path == "/" else path.lstrip("/")
            target = ROOT / "web" / name
            if not target.is_file():
                return self.json({"error": "页面文件未就绪"}, 503)
            body = target.read_bytes()
            self.send_response(200)
            self.common_headers({"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8"}[name])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        user = self.require(allow_password_change=path == "/api/me")
        if not user:
            return
        if path == "/api/me":
            return self.json({"user": {k: v for k, v in user.items() if k not in {"csrf", "auth_type"}}, "csrf": user["csrf"], "version": __version__, "lan_url": f"http://{self.server.lan_ip}:{self.server.server_port}"})
        if path in {"/api/dashboard", "/api/v1/overview", "/api/v1/items"}:
            snapshot = self.server.store.snapshot()
            snapshot["after_sales"] = self.server.after_sales.snapshot()
            if path == "/api/v1/items":
                return self.json({"items": snapshot["items"], "office_staff": snapshot.get("office_staff", []), "period": snapshot.get("period"), "generated_at": snapshot.get("generated_at")})
            return self.json(snapshot)
        if path in {"/api/after-sales", "/api/v1/after-sales"}:
            return self.json(self.server.after_sales.snapshot())
        if path == "/api/jobs/current":
            return self.json(self.server.jobs.status())
        if path == "/api/users":
            if user["role"] != "admin":
                return self.json({"error": "仅李睿琪可以管理授权账号"}, 403)
            return self.json({"users": self.server.store.users()})
        if path == "/api/tokens":
            if user["auth_type"] == "api":
                return self.json({"error": "请在网页登录后管理令牌"}, 403)
            return self.json({"tokens": self.server.store.api_tokens(user["username"])})
        if path in {"/api/reports", "/api/v1/reports"}:
            try:
                from app.reports import build_report, render_report_csv, render_report_html, render_report_brief_html
                query = parse_qs(parsed.query)
                kind = query.get("kind", ["daily"])[0]
                end = query.get("end", [""])[0]
                fmt = query.get("format", ["html"])[0]
                if fmt not in {"html", "csv", "json", "pdf"}:
                    raise ValueError("导出格式无效")
                snapshot = self.server.store.snapshot()
                snapshot["after_sales"] = self.server.after_sales.snapshot()
                report = build_report(snapshot, kind, end)
                report["version"] = __version__
                if fmt == "json":
                    return self.json(report)
                if fmt == "pdf":
                    from app.pdf_export import render_report_pdf
                    body = render_report_pdf(render_report_brief_html(report), ROOT)
                else:
                    content = render_report_csv(report) if fmt == "csv" else render_report_html(report)
                    body = content.encode("utf-8-sig" if fmt == "csv" else "utf-8")
                filename = f"生产管理_{'日报' if kind == 'daily' else '周报'}_{end}_V{__version__}.{fmt}"
                self.send_response(200)
                # 离线报表有自包含打印样式，作为下载附件交付。
                self.send_header("Content-Type", {"csv": "text/csv; charset=utf-8", "pdf": "application/pdf", "html": "text/html; charset=utf-8"}[fmt])
                ascii_filename = f"production_{kind}_{end}_V{__version__}.{fmt}"
                self.send_header("Content-Disposition", f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'' + quote(filename))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            except (ValueError, KeyError) as exc:
                return self.json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                return self.json({"error": str(exc)}, 503)
        return self.json({"error": "接口不存在"}, 404)

    def do_POST(self):
        return self.mutate("POST")

    def do_PATCH(self):
        return self.mutate("PATCH")

    def mutate(self, method):
        if not self.access() or not self.valid_origin():
            return self.json({"error": "请求来源不被允许"}, 403)
        path = urlparse(self.path).path
        try:
            data = self.body()
            if path == "/api/login" and method == "POST":
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
            user = self.require(write=True, allow_password_change=method == "POST" and path in {"/api/password", "/api/logout"})
            if not user:
                return
            if path == "/api/jobs" and method == "POST":
                if user["role"] != "admin":
                    return self.json({"error": "仅李睿琪可启动整理，其他账号可查看运行结果"}, 403)
                if data:
                    raise ValueError("整理入口不接受自定义命令、路径或提示词")
                return self.json(self.server.jobs.start(user["username"]), 202)
            if path.startswith("/api/after-sales/") and method == "PATCH":
                if user["role"] not in {"admin", "editor"}:
                    return self.json({"error": "该账号仅有查看权限"}, 403)
                return self.json({"case": self.server.after_sales.update(unquote(path[len('/api/after-sales/'):]), data, user)})
            if path == "/api/logout" and method == "POST":
                self.server.store.logout(self.session_token)
                return self.json({"ok": True}, headers={"Set-Cookie": f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            if path == "/api/password" and method == "POST":
                old, new = data.get("old_password", ""), data.get("new_password", "")
                if not isinstance(old, str) or not isinstance(new, str):
                    raise ValueError("密码格式无效")
                self.server.store.change_password(user["username"], old, new)
                return self.json({"ok": True}, headers={"Set-Cookie": f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            if path == "/api/items" and method == "POST":
                if user["role"] != "admin":
                    return self.json({"error": "手工登记事项由李睿琪管理账号执行"}, 403)
                if any(not isinstance(v, (str, type(None))) for v in data.values()):
                    raise ValueError("事项字段需为文字")
                return self.json({"item": self.server.store.add_item(data, user["username"])}, 201)
            if path.startswith("/api/items/") and method == "PATCH":
                if user["role"] not in {"admin", "editor"}:
                    return self.json({"error": "该账号仅有查看权限"}, 403)
                item_id = unquote(path[len("/api/items/"):])
                return self.json({"item": self.server.store.update_item(item_id, data, user["username"])})
            if path == "/api/users" and method == "POST":
                if user["role"] != "admin":
                    return self.json({"error": "仅李睿琪可以新增账号"}, 403)
                return self.json(self.server.store.create_user(str(data.get("username", "")), str(data.get("display_name", "")), str(data.get("role", "viewer"))), 201)
            if path.startswith("/api/users/") and method == "PATCH":
                if user["role"] != "admin":
                    return self.json({"error": "仅李睿琪可以停用账号"}, 403)
                self.server.store.manage_user(unquote(path[len("/api/users/"):]), data)
                return self.json({"ok": True})
            if path.startswith("/api/users/") and path.endswith("/reset-password") and method == "POST":
                if user["role"] != "admin":
                    return self.json({"error": "仅李睿琪可以重置员工密码"}, 403)
                username = unquote(path[len("/api/users/"):-len("/reset-password")])
                return self.json(self.server.store.reset_user_password(username))
            if path == "/api/tokens" and method == "POST":
                return self.json(self.server.store.create_api_token(user["username"], str(data.get("label", "")), int(data.get("days", 30))), 201)
            if path.startswith("/api/tokens/") and method == "PATCH":
                self.server.store.revoke_api_token(user["username"], path[len("/api/tokens/"):])
                return self.json({"ok": True})
            return self.json({"error": "接口不存在"}, 404)
        except (ValueError, KeyError, TypeError) as exc:
            return self.json({"error": str(exc)}, 400)
        except Exception:
            return self.json({"error": "服务暂时处理失败；原有数据已保留，请查看本机运行日志"}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--lan-ip", default="192.168.10.237")
    parser.add_argument("--workspace-id", default=hashlib.sha256(str(ROOT.resolve()).casefold().encode("utf-8")).hexdigest())
    args = parser.parse_args()
    store = Store(ROOT)
    server = DashboardServer((args.host, args.port), store, args.lan_ip)
    server.workspace_id = args.workspace_id
    print(f"生产管理看板 V{__version__} http://{args.lan_ip}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
