"""彩灯邮箱设置：管理员本机输入，凭据不进入接口响应或分析输入。"""
from __future__ import annotations

import imaplib
import json
import ssl
import threading

from app.jobs import write_json
from app.lantern_sources import CONFIG_PATH, SECRET_PATH, _local_file, _validate_config, _imap_string

GUARD = threading.RLock()
DEFAULT_HOST = "imap.qiye.aliyun.com"


def _raw(root):
    path = _local_file(root, CONFIG_PATH)
    if not path.exists():
        return {"schema": 1, "wechat_groups": [], "email": None}
    try:
        value = json.loads(path.read_text("utf-8-sig"))
        _validate_config(value)
        return value
    except (OSError, ValueError) as exc:
        raise ValueError("已有来源配置无法读取，请部署人员检查；未覆盖原配置") from exc


def settings(root):
    with GUARD:
        email = _raw(root).get("email")
        result = dict(email or {"host": DEFAULT_HOST, "port": 993, "account": "", "folders": ["INBOX"], "senders": [], "all_senders": True})
        result.pop("credential_file", None)
        result.setdefault("all_senders", False)
        result["configured"] = bool(email)
        result["password_saved"] = bool(email and _local_file(root, SECRET_PATH).is_file())
        result["data_directory"] = str(_local_file(root, "private").resolve())
        return result


def _candidate(root, data):
    allowed = {"host", "account", "folders", "senders", "all_senders", "password"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("邮箱设置包含不支持的字段")
    email = {key: data.get(key) for key in ("host", "account", "folders", "senders", "all_senders")}
    email.update(port=993, credential_file=SECRET_PATH)
    raw = _raw(root)
    candidate = _validate_config({**raw, "email": email})
    password = data.get("password", "")
    if not isinstance(password, str) or len(password) > 512 or any(c in password for c in "\r\n\x00"):
        raise ValueError("邮箱安全密码格式无效")
    if not password:
        old = raw.get("email") or {}
        if old.get("account") != email["account"] or old.get("host") != email["host"]:
            raise ValueError("首次配置或更换账号、服务器时，请填写对应邮箱的安全密码")
        try:
            password = json.loads(_local_file(root, SECRET_PATH).read_text("utf-8-sig"))["password"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("请填写邮箱三方客户端安全密码") from exc
        if not isinstance(password, str) or not password:
            raise ValueError("请填写邮箱三方客户端安全密码")
    return candidate, password


def save(root, data):
    with GUARD:
        candidate, password = _candidate(root, data)
        # 原子替换单个文件；先写密码再启用配置。更换邮箱时旧设置不会配旧密码。
        write_json(_local_file(root, SECRET_PATH), {"password": password, "account": candidate["email"]["account"], "host": candidate["email"]["host"]})
        write_json(_local_file(root, CONFIG_PATH), candidate)
        return settings(root)


def test_connection(root, data):
    with GUARD:
        candidate, password = _candidate(root, data)
    email = candidate["email"]
    connection = None
    try:
        connection = imaplib.IMAP4_SSL(email["host"], 993, ssl_context=ssl.create_default_context(), timeout=15)
        if connection.login(email["account"], password)[0] != "OK":
            raise ValueError("邮箱登录失败，请核对三方客户端安全密码")
        for folder in email["folders"]:
            if connection.select(_imap_string(folder), readonly=True)[0] != "OK":
                raise ValueError("邮箱文件夹无法只读打开，请检查文件夹名称")
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ValueError("邮箱连接失败，请检查IMAP权限、三方客户端安全密码、服务器与网络") from exc
    finally:
        if connection is not None:
            try:
                connection.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    return {"ok": True, "message": "连接成功，所选文件夹可以只读打开；测试未拉取邮件，请保存后开始整理。"}
