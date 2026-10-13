"""本机业务存储：原始证据、人工修改与账号权限分别保存。"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
STATUSES = {"待确认", "待处理", "处理中", "待验证", "已解决"}


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Store:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.private = self.workspace / "private"
        self.private.mkdir(parents=True, exist_ok=True)
        self.path = self.private / "production_V0.2.0.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users(
              username TEXT PRIMARY KEY,display_name TEXT NOT NULL,role TEXT NOT NULL,
              salt TEXT NOT NULL,password_hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS sessions(
              token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,csrf TEXT NOT NULL,expires TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS api_tokens(
              id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,username TEXT NOT NULL,
              label TEXT NOT NULL,created_at TEXT NOT NULL,expires TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS items(
              id TEXT PRIMARY KEY,base_json TEXT NOT NULL,override_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT NOT NULL,username TEXT NOT NULL,
              action TEXT NOT NULL,item_id TEXT,details_json TEXT NOT NULL);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            if "must_change_password" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
            conn.execute("PRAGMA journal_mode=WAL")
        self.bootstrap_users()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bootstrap_users(self):
        with self.connect() as conn:
            if conn.execute("SELECT count(*) FROM users").fetchone()[0]:
                return
            credentials = []
            for username, display, role in [("liruiqi", "李睿琪", "admin"), ("zengyue", "曾悦", "viewer")]:
                password = secrets.token_urlsafe(15)
                salt = secrets.token_hex(16)
                conn.execute("INSERT INTO users(username,display_name,role,salt,password_hash,active,must_change_password) VALUES(?,?,?,?,?,1,1)",
                             (username, display, role, salt, password_hash(password, salt)))
                label = "李睿琪管理" if role == "admin" else "厂长查看"
                credentials.append(f"{display}（{label}）\n账号：{username}\n初始密码：{password}\n")
        note = "生产管理看板 V0.2.0 本机初始账号\n请妥善保管此文件，可在登录后修改密码。不要把账号文件或API令牌上传代码仓库。\n\n" + "\n".join(credentials)
        (self.private / "初始账号_V0.2.0.txt").write_text(note, encoding="utf-8")

    def login(self, username, password):
        with self.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
            salt = user["salt"] if user else "0" * 32
            value = password_hash(password, salt)
            if not user or not hmac.compare_digest(value, user["password_hash"]):
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            expiry = (datetime.now(TZ) + timedelta(hours=12)).isoformat(timespec="seconds")
            conn.execute("DELETE FROM sessions WHERE expires<?", (now(),))
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), username, csrf, expiry))
            return {"token": token, "csrf": csrf, "user": self.safe_user(user)}

    @staticmethod
    def safe_user(user):
        return {"username": user["username"], "display_name": user["display_name"], "role": user["role"],
                "must_change_password": bool(user["must_change_password"])}

    def authenticate(self, token, api=False):
        with self.connect() as conn:
            if api:
                row = conn.execute("""SELECT u.*,t.id token_id FROM api_tokens t JOIN users u ON u.username=t.username
                  WHERE t.token_hash=? AND t.revoked=0 AND t.expires>? AND u.active=1""", (digest(token), now())).fetchone()
            else:
                row = conn.execute("""SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.username=s.username
                  WHERE s.token_hash=? AND s.expires>? AND u.active=1""", (digest(token), now())).fetchone()
            if not row or (api and row["must_change_password"]):
                return None
            result = self.safe_user(row)
            result["auth_type"] = "api" if api else "session"
            result["csrf"] = None if api else row["csrf"]
            return result

    def logout(self, token):
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))

    def change_password(self, username, old_password, new_password):
        if not 10 <= len(new_password) <= 120:
            raise ValueError("新密码需为10至120个字符")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not hmac.compare_digest(password_hash(old_password, row["salt"]), row["password_hash"]):
                raise ValueError("原密码不正确")
            if hmac.compare_digest(new_password.encode("utf-8"), old_password.encode("utf-8")):
                raise ValueError("新密码不能与原密码相同")
            salt = secrets.token_hex(16)
            conn.execute("UPDATE users SET salt=?,password_hash=?,must_change_password=0 WHERE username=?", (salt, password_hash(new_password, salt), username))
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE username=?", (username,))
            conn.execute("INSERT INTO audit(time,username,action,details_json) VALUES(?,?,?,?)",
                         (now(), username, "change_password", "{}"))

    def users(self):
        with self.connect() as conn:
            return [{**dict(r), "must_change_password": bool(r["must_change_password"])} for r in
                    conn.execute("SELECT username,display_name,role,active,must_change_password FROM users ORDER BY username")]

    def create_user(self, username, display_name, role):
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_-]{3,32}", username):
            raise ValueError("账号用3至32位字母、数字、下划线或短横线")
        if not display_name.strip() or len(display_name) > 40 or role not in {"viewer", "editor"}:
            raise ValueError("姓名或权限无效；可授权查看或处理事项")
        password, salt = secrets.token_urlsafe(15), secrets.token_hex(16)
        with self.connect() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("该账号已存在")
            conn.execute("INSERT INTO users(username,display_name,role,salt,password_hash,active,must_change_password) VALUES(?,?,?,?,?,1,1)",
                         (username, display_name.strip(), role, salt, password_hash(password, salt)))
        return {"username": username, "display_name": display_name.strip(), "role": role, "initial_password": password}

    def disable_user(self, username):
        self.manage_user(username, {"active": False})

    def manage_user(self, username, data):
        if not data or set(data) - {"active", "role"}:
            raise ValueError("仅支持启用/停用和查看/处理权限")
        if "active" in data and not isinstance(data["active"], bool):
            raise ValueError("启用状态需为布尔值")
        if "role" in data and data["role"] not in {"viewer", "editor"}:
            raise ValueError("员工权限可选查看或处理事项")
        with self.connect() as conn:
            row = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if not row or row["role"] == "admin":
                raise ValueError("不能修改李睿琪管理账号或不存在的账号")
            fields, values = [], []
            for key, value in data.items():
                fields.append(key + "=?")
                values.append(int(value) if key == "active" else value)
            conn.execute("UPDATE users SET " + ",".join(fields) + " WHERE username=?", (*values, username))
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE username=?", (username,))

    def reset_user_password(self, username):
        with self.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user or user["role"] == "admin":
                raise ValueError("李睿琪管理账号请使用本人修改密码入口")
            password, salt = secrets.token_urlsafe(15), secrets.token_hex(16)
            conn.execute("UPDATE users SET salt=?,password_hash=?,must_change_password=1 WHERE username=?", (salt, password_hash(password, salt), username))
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE username=?", (username,))
            return {"username": username, "display_name": user["display_name"], "initial_password": password}

    def reset_all_passwords(self, password, actor="liruiqi"):
        """仅由已授权本机维护调用；启动和网页路由不会自动执行批量重置。"""
        if not isinstance(password, str) or not 8 <= len(password) <= 120:
            raise ValueError("初始密码需为8至120个字符")
        with self.connect() as conn:
            usernames = [row[0] for row in conn.execute("SELECT username FROM users ORDER BY username")]
            for username in usernames:
                salt = secrets.token_hex(16)
                conn.execute("UPDATE users SET salt=?,password_hash=?,must_change_password=1 WHERE username=?",
                             (salt, password_hash(password, salt), username))
            conn.execute("DELETE FROM sessions")
            conn.execute("UPDATE api_tokens SET revoked=1")
            conn.execute("INSERT INTO audit(time,username,action,details_json) VALUES(?,?,?,?)",
                         (now(), actor, "reset_all_passwords", json.dumps({"usernames": usernames}, ensure_ascii=False)))
            return usernames

    def create_api_token(self, username, label, days):
        if not label.strip() or len(label) > 60 or days not in {7, 30, 90}:
            raise ValueError("填写令牌用途，并选择7、30或90天有效期")
        token = "dino_" + secrets.token_urlsafe(36)
        token_id = uuid.uuid4().hex
        expires = (datetime.now(TZ) + timedelta(days=days)).isoformat(timespec="seconds")
        with self.connect() as conn:
            user = conn.execute("SELECT active,must_change_password FROM users WHERE username=?", (username,)).fetchone()
            if not user or not user["active"] or user["must_change_password"]:
                raise ValueError("请先修改本人初始密码，再生成只读令牌")
            conn.execute("INSERT INTO api_tokens VALUES(?,?,?,?,?,?,0)", (token_id, digest(token), username, label.strip(), now(), expires))
        return {"id": token_id, "token": token, "expires": expires, "scope": "只读"}

    def api_tokens(self, username):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT id,label,created_at,expires,revoked FROM api_tokens WHERE username=? ORDER BY created_at DESC", (username,))]

    def revoke_api_token(self, username, token_id):
        with self.connect() as conn:
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE username=? AND id=?", (username, token_id))

    def import_snapshot(self, snapshot, connection=None):
        from app.normalize import validate_snapshot
        validate_snapshot(snapshot)
        inserted = updated = 0
        with (nullcontext(connection) if connection is not None else self.connect()) as conn:
            for item in snapshot["items"]:
                current = conn.execute("SELECT base_json FROM items WHERE id=?", (item["id"],)).fetchone()
                merged = dict(item)
                if current:
                    old = json.loads(current["base_json"])
                    evidence = {e["locator"]: e for e in old.get("evidence", [])}
                    evidence.update({e["locator"]: e for e in item.get("evidence", [])})
                    merged["evidence"] = list(evidence.values())
                    merged["first_seen"] = min(old["first_seen"], item["first_seen"])
                    merged["last_seen"] = max(old["last_seen"], item["last_seen"])
                    # 既有阶段关闭不由新的自动候选撤销；重新开启走授权人员PATCH。
                    if old.get("status") == "已解决" and item.get("status") != "已解决":
                        for key in ("status", "closed_at", "closure_scope", "review_state", "completion_source", "completed_by", "completed_by_name"):
                            if key in old:
                                merged[key] = old[key]
                    if old.get("progress_at") and old["progress_at"] > (merged.get("progress_at") or ""):
                        for key in ("progress_at", "progress_note", "progress_locator"):
                            if key in old:
                                merged[key] = old[key]
                    conn.execute("UPDATE items SET base_json=? WHERE id=?", (json.dumps(merged, ensure_ascii=False), item["id"]))
                    updated += 1
                else:
                    conn.execute("INSERT INTO items(id,base_json) VALUES(?,?)", (item["id"], json.dumps(merged, ensure_ascii=False)))
                    inserted += 1
            metadata = {k: v for k, v in snapshot.items() if k != "items"}
            metadata["imported_at"] = now()
            conn.execute("INSERT OR REPLACE INTO metadata VALUES('snapshot',?)", (json.dumps(metadata, ensure_ascii=False),))
        return {"inserted": inserted, "updated": updated}

    def snapshot(self):
        from app.briefs import list_summary
        from app.workdays import priority
        with self.connect() as conn:
            meta = conn.execute("SELECT value_json FROM metadata WHERE key='snapshot'").fetchone()
            result = json.loads(meta[0]) if meta else {"version": "0.2.0", "coverage": {}, "period": {}, "generated_at": None}
            brief_row = conn.execute("SELECT value_json FROM metadata WHERE key='list_summaries'").fetchone()
            briefs = json.loads(brief_row[0]) if brief_row else {}
            items = []
            for row in conn.execute("SELECT * FROM items"):
                base, overrides = json.loads(row["base_json"]), json.loads(row["override_json"])
                source_progress = base.get("progress_at")
                manual_evidence = overrides.pop("manual_evidence", [])
                source_last_seen = base["last_seen"]
                base.update(overrides)
                if source_progress and source_progress > (base.get("progress_at") or ""):
                    base["progress_at"] = source_progress
                base["source_last_seen"] = source_last_seen
                if isinstance(base.get("review_state"), str):
                    base["review_state"] = base["review_state"].replace("副厂", "李睿琪")
                if overrides.get("manual_updated_at"):
                    base["last_seen"] = max(source_last_seen, overrides["manual_updated_at"])
                base["evidence"] = base.get("evidence", []) + manual_evidence
                base["list_summary"] = list_summary(base, briefs.get(base["id"]))
                base["priority"] = priority(base)
                items.append(base)
            result["items"] = sorted(items, key=lambda i: (i["status"] == "已解决", not i["priority"]["urgent"], i["last_seen"]), reverse=False)
            if result.get("coverage", {}).get("limitations"):
                result["coverage"]["limitations"] = [str(note).replace("副厂", "李睿琪") for note in result["coverage"]["limitations"]]
            result["team"] = {"跟单": ["张英", "银小芹"], "机械设计": ["王星", "郭逸", "张利伟"], "李睿琪": ["本人绘图 / 协调"]}
            return result

    def set_list_summaries(self, summaries):
        """本地整理流程保存展示摘要；内容变化后由指纹自动使旧摘要失效。"""
        from app.briefs import summary_fingerprint
        items = {item["id"]: item for item in self.snapshot()["items"]}
        if not isinstance(summaries, dict) or any(
                item_id not in items or not isinstance(text, str) or not text.strip() or len(text) > 80
                for item_id, text in summaries.items()):
            raise ValueError("清单摘要需对应已有事项，每条1至80个字符")
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM metadata WHERE key='list_summaries'").fetchone()
            saved = json.loads(row[0]) if row else {}
            saved.update({item_id: {"text": text.strip(), "fingerprint": summary_fingerprint(items[item_id])}
                          for item_id, text in summaries.items()})
            conn.execute("INSERT OR REPLACE INTO metadata VALUES('list_summaries',?)",
                         (json.dumps(saved, ensure_ascii=False),))

    def update_item(self, item_id, changes, username):
        with self.connect() as conn:
            actor = conn.execute("SELECT role,display_name FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if not actor:
            raise ValueError("操作账号无效")
        is_admin = actor["role"] == "admin"
        if actor["role"] not in {"admin", "editor"}:
            raise ValueError("该账号仅有查看权限")
        allowed = {"owner", "deadline", "status", "closure_scope", "closure_note"}
        if not changes or set(changes) - allowed:
            raise ValueError("只支持负责人、期限、状态和关闭依据")
        if "status" in changes and changes["status"] not in STATUSES:
            raise ValueError("状态无效")
        if any(v is not None and not isinstance(v, str) for v in changes.values()):
            raise ValueError("字段必须为文字或空值")
        for key, limit in [("owner", 80), ("deadline", 200), ("closure_scope", 2000), ("closure_note", 2000)]:
            if len(changes.get(key) or "") > limit:
                raise ValueError("字段内容过长")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError("事项不存在")
            base, overrides = json.loads(row["base_json"]), json.loads(row["override_json"])
            note = changes.pop("closure_note", "") or ""
            if note.strip() in {"好", "好的", "收到", "OK", "ok", "安排", "已阅", "查看"}:
                raise ValueError("请填写实际处理内容，收悉或安排不能重新计时")
            if note.strip():
                overrides["progress_at"] = now()
                overrides.setdefault("progress_notes", []).append({"time": now(), "username": username, "name": actor["display_name"], "note": note.strip()})
            current_status = overrides.get("status", base["status"])
            current_scope = overrides.get("closure_scope", base.get("closure_scope"))
            changing_closed_scope = changes.get("status", current_status) == "已解决" and "closure_scope" in changes and changes["closure_scope"] != current_scope
            if changes.get("status") == "已解决" or changing_closed_scope:
                if not note.strip():
                    raise ValueError("关闭事项必须填写实际完成或验收依据")
                changes["closed_at"] = now()
                changes["review_state"] = "李睿琪已复核" if is_admin else "员工手工补录完成"
                changes["completion_source"] = "manual"
                changes["completed_by"] = username
                changes["completed_by_name"] = actor["display_name"]
                changes["closure_scope"] = changes.get("closure_scope") or (current_scope if current_status == "已解决" else None) or "仅本事项所述工作，人工补录完成；以完成说明为准"
                overrides.setdefault("manual_evidence", []).append({"locator": "manual:" + uuid.uuid4().hex, "time": now(), "sender": actor["display_name"], "text": note.strip()})
            elif "status" in changes:
                changes["closed_at"] = None
                changes["review_state"] = "李睿琪人工更新" if is_admin else "处理人员更新，待李睿琪复核"
            overrides.update(changes)
            overrides["manual_updated_at"] = now()
            conn.execute("UPDATE items SET override_json=? WHERE id=?", (json.dumps(overrides, ensure_ascii=False), item_id))
            conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)", (now(), username, "update_item", item_id, json.dumps({**changes, "closure_note": note}, ensure_ascii=False)))
        return next(i for i in self.snapshot()["items"] if i["id"] == item_id)

    def add_item(self, data, username):
        title = (data.get("title") or "").strip()
        category = data.get("category", "任务")
        summary = (data.get("summary") or "").strip()
        owner, deadline = data.get("owner") or None, data.get("deadline") or None
        if not title or len(title) > 160 or category not in {"问题", "任务", "资料", "确认"}:
            raise ValueError("请填写有效标题和事项分类")
        if len(summary) > 4000 or len(owner or "") > 80 or len(deadline or "") > 200:
            raise ValueError("事项内容过长")
        timestamp, item_id = now(), "MANUAL-" + uuid.uuid4().hex
        item = {"id": item_id, "title": title, "category": category, "status": "待处理", "owner": owner,
                "deadline": deadline, "first_seen": timestamp, "last_seen": timestamp, "summary": summary,
                "next_step": "按已登记要求落实处理、完成后提交验证", "source_id": "manual", "group": "手工登记",
                "evidence": [{"locator": "manual:" + uuid.uuid4().hex, "time": timestamp, "sender": username,
                              "text": summary or "李睿琪手工登记本事项"}], "closure_scope": None,
                "review_state": "李睿琪手工登记", "closed_at": None}
        with self.connect() as conn:
            conn.execute("INSERT INTO items(id,base_json) VALUES(?,?)", (item_id, json.dumps(item, ensure_ascii=False)))
            conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                         (timestamp, username, "add_item", item_id, json.dumps({"title": title}, ensure_ascii=False)))
        return item
