"""钉钉授权码登录；仅本机私有配置，绑定既有账号，不按昵称赋权。"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import ipaddress
import json
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from app.store import TZ, digest, now

OAUTH_COOKIE = "dino_ding_oauth"
BIND_COOKIE = "dino_ding_binding"
SCOPE = "openid corpid Contact.User.Read"


class LoginError(ValueError):
    def __init__(self, message, diagnostic="login_rejected"):
        super().__init__(message)
        self.diagnostic = diagnostic


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class DingTalkLogin:
    def __init__(self, store, *, public_origin=None):
        self.store = store
        self.config = None
        self._app_token = None
        self._token_lock = threading.Lock()
        self.public_origin = public_origin
        config_path = store.private / "dingtalk" / (store.path.stem + ".json")
        if not config_path.exists():
            return
        try:
            value = json.loads(config_path.read_text("utf-8-sig"))
            if value.get("enabled") is not True:
                return
            for key in ("client_id", "corp_id"):
                if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", value.get(key, "")):
                    raise ValueError()
            secret = value.get("client_secret")
            if (not isinstance(secret, str) or not 12 <= len(secret) <= 256
                    or "*" in secret or any(c.isspace() or ord(c) < 32 for c in secret)):
                raise ValueError()
            parsed = urlsplit(value.get("redirect_uri", ""))
            address = ipaddress.ip_address(parsed.hostname or "")
            if (parsed.scheme != "http" or not (address.is_loopback or address.is_private)
                    or parsed.username or parsed.password or not parsed.port
                    or parsed.path != "/auth/dingtalk/callback" or parsed.query or parsed.fragment):
                raise ValueError()
            self.config = {k: value[k] for k in ("client_id", "client_secret", "corp_id", "redirect_uri")}
            self.origin = f"{parsed.scheme}://{parsed.netloc}"
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # 配置问题不能阻断原账号登录，不向匿名调用者泄露字段或密钥。
            return
        with store.connect() as conn:
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS dingtalk_states(
                state_hash TEXT PRIMARY KEY,cookie_hash TEXT NOT NULL,expires TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS dingtalk_pending(
                token_hash TEXT PRIMARY KEY,corp_id TEXT NOT NULL,subject_hash TEXT NOT NULL,expires TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS dingtalk_identities(
                corp_id TEXT NOT NULL,subject_hash TEXT NOT NULL,username TEXT NOT NULL,created_at TEXT NOT NULL,
                PRIMARY KEY(corp_id,subject_hash),UNIQUE(corp_id,username));
              CREATE TABLE IF NOT EXISTS dingtalk_workbench_states(
                nonce_hash TEXT PRIMARY KEY,cookie_hash TEXT NOT NULL,expires TEXT NOT NULL);
            """)

    @property
    def enabled(self):
        return self.config is not None

    def status(self, pending="", username=None):
        result = {"enabled": self.enabled, "binding_pending": False, "bound": False}
        if not self.enabled:
            return result
        result["login_url"] = self.origin + "/auth/dingtalk/start"
        with self.store.connect() as conn:
            result["binding_pending"] = bool(pending and conn.execute(
                "SELECT 1 FROM dingtalk_pending WHERE token_hash=? AND expires>? AND corp_id=?",
                (digest(pending), now(), self.config["corp_id"])).fetchone())
            if username:
                result["bound"] = bool(conn.execute(
                    "SELECT 1 FROM dingtalk_identities WHERE corp_id=? AND username=?",
                    (self.config["corp_id"], username)).fetchone())
        return result

    def start(self, *, public=False):
        if not self.enabled:
            raise LoginError("钉钉登录尚未配置，请使用原账号登录")
        state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = (datetime.now(TZ) + timedelta(minutes=10)).isoformat(timespec="seconds")
        with self.store.connect() as conn:
            conn.execute("DELETE FROM dingtalk_states WHERE expires<?", (now(),))
            conn.execute("DELETE FROM dingtalk_pending WHERE expires<?", (now(),))
            if conn.execute("SELECT count(*) FROM dingtalk_states").fetchone()[0] >= 500:
                raise LoginError("登录请求较多，请稍后重试")
            conn.execute("INSERT INTO dingtalk_states VALUES(?,?,?)", (digest(state), digest(cookie), expires))
        return "https://login.dingtalk.com/oauth2/auth?" + urlencode({
            "client_id": self.config["client_id"], "redirect_uri": (self.public_origin + '/auth/dingtalk/callback') if public and self.public_origin else self.config["redirect_uri"],
            "response_type": "code", "state": state, "prompt": "consent", "scope": SCOPE,
            "corpId": self.config["corp_id"],
        }), cookie

    def callback(self, state, cookie, code):
        if not self.enabled or not all(isinstance(x, str) and 1 <= len(x) <= 4096 for x in (state, cookie, code)):
            raise LoginError("本次钉钉登录已失效，请重新点击钉钉登录")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM dingtalk_states WHERE state_hash=? AND cookie_hash=? AND expires>?",
                               (digest(state), digest(cookie), now())).fetchone()
            if not row:
                raise LoginError("本次钉钉登录已失效，请重新点击钉钉登录")
            conn.execute("DELETE FROM dingtalk_states WHERE state_hash=?", (digest(state),))
        # 在网络交换前消费state；回调重放或并发请求均不能再次交换授权码。
        subject = self.exchange(code)
        return self.finish_identity(subject)

    def workbench_start(self):
        if not self.enabled:
            raise LoginError("钉钉登录尚未配置，请使用原账号登录")
        nonce, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expiry = (datetime.now(TZ) + timedelta(minutes=5)).isoformat(timespec="seconds")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM dingtalk_workbench_states WHERE expires<?", (now(),))
            if conn.execute("SELECT count(*) FROM dingtalk_workbench_states").fetchone()[0] >= 500:
                raise LoginError("登录请求较多，请稍后重试")
            conn.execute("INSERT INTO dingtalk_workbench_states VALUES(?,?,?)", (digest(nonce), digest(cookie), expiry))
        return {"nonce": nonce, "corpId": self.config["corp_id"]}, cookie

    def workbench_login(self, nonce, cookie, code):
        if not self.enabled or not all(isinstance(x, str) and 1 <= len(x) <= 4096 for x in (nonce, cookie, code)):
            raise LoginError("本次钉钉识别已失效，请重新打开工作台应用")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT 1 FROM dingtalk_workbench_states WHERE nonce_hash=? AND cookie_hash=? AND expires>?",
                               (digest(nonce), digest(cookie), now())).fetchone()
            if not row:
                raise LoginError("本次钉钉识别已失效，请重新打开工作台应用")
            conn.execute("DELETE FROM dingtalk_workbench_states WHERE nonce_hash=?", (digest(nonce),))
        return self.finish_identity(self.exchange_workbench(code))

    def exchange_workbench(self, code):
        # 固定企业应用凭据换取固定企业token，不接受客户端传来的企业或用户身份。
        with self._token_lock:
            if not self._app_token or self._app_token[1] <= time.monotonic():
                token = self._api("https://api.dingtalk.com/v1.0/oauth2/" + self.config["corp_id"] + "/token", {
                    "client_id": self.config["client_id"], "client_secret": self.config["client_secret"],
                    "grant_type": "client_credentials"})
                access = token.get("access_token", "")
                if not isinstance(access, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{8,4096}", access):
                    raise LoginError("钉钉应用身份校验失败，请使用原账号登录", "workbench_app_token_invalid")
                duration = token.get("expires_in", 0)
                if not isinstance(duration, (int, float)) or not 120 <= duration <= 86400:
                    raise LoginError("钉钉应用身份校验失败，请使用原账号登录", "workbench_token_expiry_invalid")
                self._app_token = (access, time.monotonic() + min(duration - 60, 3600))
            access = self._app_token[0]
        profile = self._api("https://oapi.dingtalk.com/topapi/v2/user/getuserinfo?" + urlencode({"access_token": access}), {"code": code})
        if profile.get("errcode") != 0:
            if profile.get("errcode") in (40014, 42001):
                with self._token_lock:
                    self._app_token = None
            raise LoginError("钉钉身份识别未完成，请重新打开工作台应用或使用原账号登录", "workbench_identity_rejected")
        result = profile.get("result")
        subject = result.get("unionid") if isinstance(result, dict) else None
        if not isinstance(subject, str) or not 1 <= len(subject) <= 256 or any(ord(c) < 32 for c in subject):
            raise LoginError("钉钉未返回有效身份，请使用原账号登录", "workbench_subject_invalid")
        try:
            from app.dingtalk_notifications import record_leader_route
            record_leader_route(self.store, self.config['corp_id'], subject, result.get('userid'))
        except (OSError, ValueError, TypeError, KeyError):
            # 通知路由配置异常不能影响原有免登；身份和权限仍由finish_identity校验。
            pass
        return subject

    def finish_identity(self, subject):
        subject_hash = digest(subject)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            binding = conn.execute("SELECT username FROM dingtalk_identities WHERE corp_id=? AND subject_hash=?",
                                   (self.config["corp_id"], subject_hash)).fetchone()
            if binding:
                user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (binding["username"],)).fetchone()
                if not user:
                    raise LoginError("看板账号已停用，请联系李睿琪")
                return {"session": self._session(conn, user)}
            pending = secrets.token_urlsafe(32)
            expires = (datetime.now(TZ) + timedelta(minutes=10)).isoformat(timespec="seconds")
            conn.execute("INSERT INTO dingtalk_pending VALUES(?,?,?,?)",
                         (digest(pending), self.config["corp_id"], subject_hash, expires))
            return {"pending": pending}

    def exchange(self, code):
        token = self._api("https://api.dingtalk.com/v1.0/oauth2/userAccessToken", {
            "clientId": self.config["client_id"], "clientSecret": self.config["client_secret"],
            "code": code, "grantType": "authorization_code",
        })
        if token.get("corpId") != self.config["corp_id"]:
            raise LoginError("请选择公司钉钉组织后重新登录", "enterprise_mismatch")
        access = token.get("accessToken", "")
        if not isinstance(access, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{8,4096}", access):
            raise LoginError("钉钉授权未完成，请重试")
        profile = self._api("https://api.dingtalk.com/v1.0/contact/users/me", access=access)
        subject = profile.get("unionId")
        if not isinstance(subject, str) or not 1 <= len(subject) <= 256 or any(ord(c) < 32 for c in subject):
            raise LoginError("钉钉未返回有效身份，请重试")
        return subject

    @staticmethod
    def _api(url, payload=None, access=None):
        headers = {"Content-Type": "application/json"}
        if access:
            headers["x-acs-dingtalk-access-token"] = access
        request = urllib.request.Request(url, headers=headers, data=(
            json.dumps(payload).encode() if payload is not None else None))
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=12) as response:
                value = json.loads(response.read(65536))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except urllib.error.HTTPError as error:
            # 仅保存接口阶段、状态码及受限错误标识，绝不记录授权码、令牌或响应原文。
            provider_code = "unknown"
            try:
                candidate = json.loads(error.read(65536)).get("code", "")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,79}", candidate):
                    provider_code = candidate
            except (OSError, ValueError, AttributeError):
                pass
            phase = "identity" if access else "token"
            raise LoginError("暂时无法完成钉钉授权，请稍后重试或使用原账号登录",
                             f"{phase}_http_{error.code}_{provider_code}") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise LoginError("暂时无法完成钉钉授权，请稍后重试或使用原账号登录", "api_unavailable") from None

    def record_failure(self, error):
        """覆盖单条本机诊断；只含代码中的固定原因或受限接口错误标识。"""
        diagnostic = getattr(error, "diagnostic", "invalid_callback")
        if not isinstance(diagnostic, str) or not re.fullmatch(r"[A-Za-z0-9_.]{1,120}", diagnostic):
            diagnostic = "login_rejected"
        try:
            path = self.store.private / "dingtalk" / (self.store.path.stem + ".last_failure.json")
            path.write_text(json.dumps({"time": now(), "reason": diagnostic}), "utf-8")
        except OSError:
            pass

    def bind_pending(self, pending, session_token):
        if not self.enabled or not pending:
            raise LoginError("钉钉绑定已过期，请重新使用钉钉登录")
        try:
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                identity = conn.execute("SELECT * FROM dingtalk_pending WHERE token_hash=? AND expires>? AND corp_id=?",
                                        (digest(pending), now(), self.config["corp_id"])).fetchone()
                user = conn.execute("SELECT u.* FROM sessions s JOIN users u ON u.username=s.username "
                                    "WHERE s.token_hash=? AND s.expires>? AND u.active=1 AND u.must_change_password=0",
                                    (digest(session_token), now())).fetchone()
                if not identity or not user:
                    raise LoginError("绑定验证已失效，请重新登录后重试")
                existing = conn.execute("SELECT * FROM dingtalk_identities WHERE corp_id=? AND (username=? OR subject_hash=?)",
                                        (identity["corp_id"], user["username"], identity["subject_hash"])).fetchall()
                if any(row["username"] != user["username"] or row["subject_hash"] != identity["subject_hash"] for row in existing):
                    raise LoginError("这个钉钉身份或看板账号已绑定，请先由本人解绑，不会覆盖已有绑定")
                conn.execute("INSERT OR IGNORE INTO dingtalk_identities VALUES(?,?,?,?)",
                             (identity["corp_id"], identity["subject_hash"], user["username"], now()))
                conn.execute("DELETE FROM dingtalk_pending WHERE token_hash=?", (digest(pending),))
                conn.execute("INSERT INTO audit(time,username,action,details_json) VALUES(?,?,?,?)",
                             (now(), user["username"], "bind_dingtalk", "{}"))
        except sqlite3.IntegrityError:
            raise LoginError("账号绑定发生冲突，请刷新后重试") from None

    def cancel_pending(self, pending):
        if self.enabled and pending:
            with self.store.connect() as conn:
                conn.execute("DELETE FROM dingtalk_pending WHERE token_hash=?", (digest(pending),))

    def unbind(self, username):
        if not self.enabled:
            raise LoginError("钉钉登录未启用")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM dingtalk_identities WHERE corp_id=? AND username=?",
                         (self.config["corp_id"], username))
            # 解绑后吊销此账号全部会话，需要重新登录；只读API令牌保持既有规则。
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
            conn.execute("INSERT INTO audit(time,username,action,details_json) VALUES(?,?,?,?)",
                         (now(), username, "unbind_dingtalk", "{}"))

    @staticmethod
    def _session(conn, user):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        expiry = (datetime.now(TZ) + timedelta(hours=12)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM sessions WHERE expires<?", (now(),))
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), user["username"], csrf, expiry))
        return {"token": token, "csrf": csrf, "user": {key: user[key] for key in
                ("username", "display_name", "role", "must_change_password")}}
