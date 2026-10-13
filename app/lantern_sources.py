"""彩灯独立来源配置与最近七天只读采集；不使用恐龙部门默认来源。"""
from __future__ import annotations

import hashlib
import imaplib
import json
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from html import unescape
from pathlib import Path
import re
import ssl
import subprocess

from app.jobs import codex_environment
from app.store import TZ


CONFIG_PATH = "private/lantern_sources.json"
SECRET_PATH = "private/lantern_email_secret.json"
MAX_EMAIL_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_EMAILS = 1000
MAX_BODY_CHARS = 16000
EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}")


def _local_file(root, path):
    """拒绝目录联接/符号链接及越界文件，业务文件必须属于指定本机目录。"""
    root = Path(root).resolve()
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    try:
        path.absolute().relative_to(root)
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("彩灯来源文件不在授权的本机目录内") from exc
    cursor = path
    while cursor != root:
        if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
            raise ValueError("彩灯来源文件不能使用符号链接或目录联接")
        cursor = cursor.parent
    return path


def _clean_string(value, label, limit=300):
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > limit
            or re.search(r"[\x00-\x1f\x7f]", value)):
        raise ValueError(label + "需为明确的文字，不能含控制字符或多余空格")
    return value


def _validate_config(raw):
    if not isinstance(raw, dict) or set(raw) - {"schema", "schema_version", "wechat_groups", "email"}:
        raise ValueError("彩灯来源配置结构无效，请部署Codex按安装说明配置")
    schema = raw.get("schema", raw.get("schema_version"))
    if type(schema) is not int or schema != 1:
        raise ValueError("彩灯来源配置schema必须为1")
    groups = raw.get("wechat_groups", [])
    if (not isinstance(groups, list) or len(groups) > 50 or
            any(not isinstance(group, str) for group in groups)):
        raise ValueError("彩灯微信群清单最多50个精确群名")
    groups = [_clean_string(group, "彩灯群完整名称", 200) for group in groups]
    if len(set(groups)) != len(groups):
        raise ValueError("彩灯微信群名不能重复；重名群须部署人员先核实")
    email = raw.get("email")
    if email is not None:
        keys = {"host", "port", "account", "folders", "senders", "credential_file"}
        if not isinstance(email, dict) or set(email) != keys:
            raise ValueError("阿里企业邮箱配置需明确host、port、account、folders、senders及credential_file")
        host = _clean_string(email["host"], "邮箱IMAP服务器", 253)
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
            raise ValueError("邮箱IMAP服务器必须由企业管理员确认，不能含URL、端口或路径")
        if type(email["port"]) is not int or email["port"] != 993:
            raise ValueError("邮箱仅支持证书验证的IMAP SSL端口993")
        account = _clean_string(email["account"], "企业邮箱账号", 254)
        if not EMAIL_ADDRESS.fullmatch(account) or any(mark in account for mark in ("*", "?")):
            raise ValueError("企业邮箱账号需为完整邮箱地址")
        folders, senders = email["folders"], email["senders"]
        if not isinstance(folders, list) or not 1 <= len(folders) <= 50:
            raise ValueError("需明确1至50个服务器邮箱文件夹")
        folders = [_clean_string(item, "服务器邮箱文件夹", 200) for item in folders]
        if any(not item.isascii() for item in folders) or len(set(folders)) != len(folders):
            raise ValueError("邮箱文件夹须为不重复的ASCII服务器名称；中文显示名请部署人员核对映射")
        if not isinstance(senders, list) or not 1 <= len(senders) <= 100:
            raise ValueError("需明确1至100个允许读取的发件邮箱")
        senders = [_clean_string(item, "允许读取的发件邮箱", 254).casefold() for item in senders]
        if (any(not EMAIL_ADDRESS.fullmatch(item) or any(mark in item for mark in ("*", "?")) for item in senders)
                or len(set(senders)) != len(senders)):
            raise ValueError("发件范围需为不重复的完整邮箱地址，不能使用通配符")
        if email["credential_file"] != SECRET_PATH:
            raise ValueError("邮箱授权只能存放在private/lantern_email_secret.json，本配置不接受密码或外部路径")
        email = {"host": host, "port": 993, "account": account, "folders": folders,
                 "senders": senders, "credential_file": SECRET_PATH}
    if not groups and not email:
        raise ValueError("彩灯来源尚未配置，请部署Codex现场配置指定彩灯群或阿里企业邮箱")
    return {"schema": 1, "wechat_groups": groups, "email": email}


def load_sources(root):
    path = _local_file(root, CONFIG_PATH)
    if not path.is_file():
        raise ValueError("彩灯来源尚未配置，请部署Codex在本机建立private/lantern_sources.json")
    try:
        raw = json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError("彩灯来源配置无法读取，请部署Codex检查JSON格式") from exc
    return _validate_config(raw)


def _window():
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=6), today + timedelta(days=1)


def _when(value):
    if not isinstance(value, str):
        raise ValueError("采集消息时间缺失")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("采集消息时间格式无效") from exc
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _collect_wechat(root, folder, groups, start, end):
    runtime = _local_file(root, "vendor/wechat/python/python.exe")
    collector = _local_file(root, "scripts/collect_scoped_wechat_V0.1.3.py")
    if not runtime.is_file() or not collector.is_file():
        raise ValueError("本机缺少完整微信/语音组件，请部署Codex安装同版本完整运行包")
    target = folder / "wechat"
    target.mkdir(parents=True, exist_ok=True)
    command = [str(runtime), "-X", "utf8", "-B", str(collector), "--scope", "lantern", "--phase", "export",
               "--group-indices", ",".join(str(index) for index in range(1, len(groups) + 1)),
               "--start", start.date().isoformat(), "--end-exclusive", end.date().isoformat(),
               "--media", "--transcribe-voices", "--out", str(target)]
    try:
        with (folder / "wechat.log").open("wb") as log:
            result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    timeout=2700, env=codex_environment(),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("彩灯微信来源采集未完成，请部署人员检查本机组件与绑定") from exc
    if result.returncode:
        raise ValueError("彩灯微信来源采集失败，请部署人员检查指定群授权与本轮日志")
    path = _local_file(target, "collection_manifest_V0.1.3.json")
    try:
        manifest = json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError("彩灯微信采集清单缺失或格式无效") from exc
    rows = manifest.get("export_results", [])
    expected = {f"lantern_{index:02}": group for index, group in enumerate(groups, 1)}
    actual_end = _when(manifest.get("end_exclusive"))
    if (manifest.get("status") != "exported" or manifest.get("sensitive_cache_cleanup") is not True
            or not isinstance(rows, list) or len(rows) != len(expected)
            or {row.get("source_id") for row in rows if isinstance(row, dict)} != set(expected)
            or _when(manifest.get("start_inclusive")) != start or not start < actual_end <= end):
        raise ValueError("彩灯微信采集范围、日期或敏感缓存清理未通过核验")
    messages, limitations = [], ["微信本轮实际采集截止：" + actual_end.isoformat(timespec="seconds") + "；之后消息不在本轮覆盖内。"]
    seen = set()
    for row in rows:
        source_id = row["source_id"]
        if (row.get("group") != expected[source_id] or row.get("binding_verified") is not True
                or row.get("exact_match_count") != 1):
            raise ValueError("彩灯群名或精确绑定不符，不能读取其他群替代")
        if row.get("status") == "verified_empty_range":
            if row.get("message_count") != 0 or not row.get("query_audit", {}).get("sql_date_filter"):
                raise ValueError("彩灯群空范围缺少真实查询依据")
            limitations.append("微信:" + row["group"] + "在本轮日期内未查询到消息。")
            continue
        if row.get("status") != "exported" or not isinstance(row.get("source_json"), str):
            raise ValueError("彩灯指定群未完整导出，不能宣称整理覆盖全部来源")
        source = _local_file(target, row["source_json"])
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise ValueError("彩灯微信源文件缺失") from exc
        if hashlib.sha256(content).hexdigest() != row.get("source_sha256"):
            raise ValueError("彩灯微信源文件SHA256核验失败")
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("彩灯微信源文件格式无效") from exc
        raw_messages = data.get("messages")
        if (not isinstance(raw_messages, list) or len(raw_messages) != row.get("message_count")
                or data.get("message_count") != len(raw_messages)):
            raise ValueError("彩灯微信消息数量核验不符")
        for raw in raw_messages:
            when = _when(raw.get("time"))
            if not start <= when < actual_end:
                raise ValueError("彩灯微信消息超出本轮最近7天授权范围")
            if raw.get("source_db") is None or raw.get("local_id") is None:
                raise ValueError("彩灯微信消息缺少源数据库定位")
            ref = f"wechat:{source_id}:{raw['source_db']}:{raw['local_id']}"
            if ref in seen:
                raise ValueError("彩灯微信消息定位重复")
            seen.add(ref)
            text = str(raw.get("content") or "")
            transcript = str(raw.get("transcript") or "")
            if transcript:
                text += "\n[本地语音转写，待核听] " + transcript
                limitations.append("微信语音使用本地转写，未核听原音，需人工复核。")
            if raw.get("media") or raw.get("type") in {3, 34, 43, 49, "image", "video", "file", "voice"}:
                limitations.append("微信图片/视频像素及附件正文未送入本轮文字整理；已采集媒体不代表已审阅。")
            messages.append({"ref": ref, "time": when.isoformat(timespec="seconds"),
                             "sender": str(raw.get("sender") or "[发送人待核对]"),
                             "source": "微信:" + row["group"], "text": text or "[消息正文待核对]"})
    return messages, limitations


def _imap_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _metadata_bytes(parts):
    return b" ".join(part[0] if isinstance(part, tuple) else part
                     for part in parts if isinstance(part, (bytes, tuple)))


def _internal_date(metadata):
    match = re.search(rb'INTERNALDATE "(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)"', metadata)
    if not match:
        raise ValueError("邮件缺少可核验的服务器内部收件时间")
    values = match.groups()
    months = {name: index for index, name in enumerate((b"Jan", b"Feb", b"Mar", b"Apr", b"May", b"Jun", b"Jul", b"Aug", b"Sep", b"Oct", b"Nov", b"Dec"), 1)}
    try:
        offset = timedelta(hours=int(values[7]), minutes=int(values[8]))
        if values[6] == b"-":
            offset = -offset
        return datetime(int(values[2]), months[values[1].title()], int(values[0]),
                        int(values[3]), int(values[4]), int(values[5]), tzinfo=timezone(offset)).astimezone(TZ)
    except (ValueError, KeyError) as exc:
        raise ValueError("邮件服务器内部收件时间格式无效") from exc


def _email_text(message, destination, prefix):
    plain, html, attachments, limitations = [], [], [], []
    def body_parts(part):
        # 邮件附件自身可能也是multipart；不能把其子正文当成授权发件人的正文。
        if part.get_filename() or part.get_content_disposition() == "attachment":
            yield part
        elif part.is_multipart():
            for child in part.iter_parts():
                yield from body_parts(child)
        else:
            yield part
    for index, part in enumerate(body_parts(message)):
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if filename or disposition == "attachment":
            data = part.get_payload(decode=True)
            if data is None:
                nested = part.get_payload()
                data = b"\n".join(child.as_bytes() for child in nested) if isinstance(nested, list) else part.as_bytes()
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", str(filename or "附件")).strip(" .")[:140] or "附件"
            if len(data) > MAX_ATTACHMENT_BYTES:
                limitations.append("邮件附件" + name + "超过10MB，未留存或解析，请人工核对。")
                continue
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / f"{prefix}_{index}_{name}"
            path.write_bytes(data)
            attachments.append(name)
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            limitations.append("邮件内嵌图片及其他非文字内容未送入本轮整理，需人工核对。")
            continue
        data = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = data.decode(charset, errors="replace")
        except LookupError:
            text = data.decode("utf-8", errors="replace")
            limitations.append("邮件声明了未知字符集，正文使用UTF-8替换解码，需人工复核。")
        (plain if part.get_content_type() == "text/plain" else html).append(text)
    body = "\n".join(plain) if plain else "\n".join(html)
    if not plain and html:
        body = unescape(re.sub(r"<[^>]*>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", "", body)))
        limitations.append("邮件HTML已转为文字；原始版式、远程图片和链接内容未审阅。")
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
        limitations.append("邮件正文超过16000字符已截断，本轮覆盖不完整，需人工查看原邮件。")
    if attachments:
        body += "\n[附件已在本轮私有目录留存，正文未解析] " + "、".join(attachments)
        limitations.append("邮件附件仅本机留存，附件正文未送入本轮整理，需人工核对。")
    return body, limitations


def _collect_email(root, folder, config, start, end):
    secret_path = _local_file(root, config["credential_file"])
    try:
        secret = json.loads(secret_path.read_text("utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError("阿里企业邮箱授权文件尚未就绪，请部署Codex让本人在本机输入授权信息") from exc
    if (not isinstance(secret, dict) or set(secret) != {"password"} or
            not isinstance(secret["password"], str) or not secret["password"]):
        raise ValueError("邮箱本机授权文件只能包含password，不能为空")
    connection = None
    messages, limitations = [], []
    total = 0
    # IMAP日期使用英文月份，独立于Windows区域语言。
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    date_arg = lambda value: f"{value.day:02}-{months[value.month - 1]}-{value.year}"
    try:
        connection = imaplib.IMAP4_SSL(config["host"], config["port"], ssl_context=ssl.create_default_context(), timeout=30)
        if connection.login(config["account"], secret["password"])[0] != "OK":
            raise ValueError("邮箱登录未通过，请本人核对本机授权")
        for mailbox in config["folders"]:
            if connection.select(_imap_string(mailbox), readonly=True)[0] != "OK":
                raise ValueError("指定邮箱文件夹无法只读打开，请部署人员核对服务器名称")
            uids = set()
            for sender in config["senders"]:
                # IMAP日期搜索忽略时区；宽一天只取候选UID/元数据，正文仍严格限制中国7日窗口。
                status, results = connection.uid("search", None, "SINCE", date_arg(start - timedelta(days=1)),
                                                  "BEFORE", date_arg(end + timedelta(days=1)), "FROM", _imap_string(sender))
                if status != "OK" or not isinstance(results, list):
                    raise ValueError("指定发件人邮箱搜索失败，不能宣称完整覆盖")
                for row in results:
                    if not isinstance(row, bytes):
                        raise ValueError("邮箱UID搜索结果格式无效")
                    for uid in row.split():
                        if not re.fullmatch(rb"[1-9][0-9]*", uid):
                            raise ValueError("邮箱返回无效UID")
                        uids.add(uid)
            total += len(uids)
            if total > MAX_EMAILS:
                raise ValueError("指定范围超过1000封邮件，本轮未完整采集，请部署人员缩小发件人或文件夹范围")
            folder_hash = hashlib.sha256(mailbox.encode("ascii")).hexdigest()[:12]
            for uid in sorted(uids, key=int):
                status, metadata = connection.uid("fetch", uid, "(RFC822.SIZE INTERNALDATE)")
                if status != "OK" or not isinstance(metadata, list):
                    raise ValueError("邮件大小与收件时间核验失败")
                header = _metadata_bytes(metadata)
                size = re.search(rb"RFC822\.SIZE (\d+)", header, re.I)
                if not size or int(size[1]) > MAX_EMAIL_BYTES:
                    raise ValueError("邮件缺少大小或超过20MB，本轮未完整采集，请人工处理")
                when = _internal_date(header)
                if not start <= when < end:
                    continue
                status, fetched = connection.uid("fetch", uid, "(BODY.PEEK[] INTERNALDATE)")
                if status != "OK" or not isinstance(fetched, list):
                    raise ValueError("邮件只读正文读取失败")
                literals = [part for part in fetched if isinstance(part, tuple) and len(part) == 2 and isinstance(part[1], bytes)]
                if len(literals) != 1:
                    raise ValueError("邮件正文返回格式无效")
                body_header, raw = literals[0]
                if len(raw) > MAX_EMAIL_BYTES:
                    raise ValueError("邮件正文超过20MB，本轮未完整采集")
                if len(raw) != int(size[1]):
                    raise ValueError("邮件正文长度与服务器大小不符，本轮采集不完整")
                body_time = _internal_date(body_header)
                if body_time != when or not start <= body_time < end:
                    raise ValueError("邮件正文与内部收件时间核验不符")
                message = BytesParser(policy=policy.default).parsebytes(raw)
                senders = {address.casefold() for _, address in getaddresses(message.get_all("From", []))}
                if len(senders) != 1 or not senders <= set(config["senders"]):
                    raise ValueError("邮件实际发件人超出明确授权范围")
                prefix = folder_hash + "_" + uid.decode("ascii")
                text, part_limits = _email_text(message, folder / "email_attachments", prefix)
                limitations.extend(part_limits)
                messages.append({"ref": "email:" + folder_hash + ":" + uid.decode("ascii"),
                                 "time": when.isoformat(timespec="seconds"), "sender": next(iter(senders)),
                                 "source": "邮箱:" + config["account"] + "/" + mailbox,
                                 "text": "主题：" + str(message.get("Subject", "")) + "\n" + (text or "[正文为空或未解析]")})
        if not messages:
            limitations.append("阿里企业邮箱指定文件夹/发件人在本轮最近7天内未查询到可读取邮件。")
    except ValueError:
        raise
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise ValueError("阿里企业邮箱只读采集失败，请部署人员核对服务器、证书、网络及本机授权；未发送邮件") from exc
    finally:
        if connection is not None:
            try:
                connection.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    return messages, limitations


def collect_sources(root, folder, config):
    root = Path(root).resolve()
    base = _local_file(root, "private/lantern_jobs")
    folder = _local_file(base, folder)
    folder.mkdir(parents=True, exist_ok=True)
    config = _validate_config(config)
    start, end = _window()
    messages, limitations = [], ["本轮仅覆盖明确配置的彩灯来源及中国日期最近7天；整理发现须人工复核。"]
    if config["wechat_groups"]:
        rows, limits = _collect_wechat(root, folder, config["wechat_groups"], start, end)
        messages.extend(rows)
        limitations.extend(limits)
    if config["email"]:
        rows, limits = _collect_email(root, folder, config["email"], start, end)
        messages.extend(rows)
        limitations.extend(limits)
    refs = [message["ref"] for message in messages]
    if len(set(refs)) != len(refs):
        raise ValueError("来源消息引用重复，未导入本轮结果")
    return {"messages": messages, "limitations": list(dict.fromkeys(limitations))}
