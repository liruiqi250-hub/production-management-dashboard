"""本轮已授权生产群的只读日期范围采集辅助工具 V0.1.0。"""
from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import io
import json
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "0.1.0"
WORKSPACE = Path(__file__).resolve().parents[1]
RUNTIME = Path(r"D:\360MoveData\Users\ASUS\Documents\ChatGPT\售后\automation\exporter\v1.3.4")
SOURCE = RUNTIME / "source" / "wechat-chat-export-1.3.4"
PRIOR_ACCOUNT_BINDING = RUNTIME.parents[1] / "private" / "monthly_binding_candidate_V1.1.0.private.json"
TZ = timezone(timedelta(hours=8))
GROUPS = [
    "生产资料群", "彩灯传动沟通群", "李婷订单沟通群", "曾霞订单沟通群",
    "刘欢订单沟通群", "谢梦订单沟通群", "刘佳订单沟通交流群", "范紫薇订单沟通群",
    "呙小凤订单沟通群", "王雪珂订单沟通群", "刘佳丽订单沟通群", "龚娟订单沟通群",
    "缪如玉订单沟通群", "董雨婷订单沟通群",
]


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def group_rows(db, name):
    found = []
    for rel, path, _ in db._db_files:
        if Path(path).name != "contact.db":
            continue
        conn = db._open(rel)
        try:
            found.extend(dict(r) for r in conn.execute(
                "SELECT username,nick_name,remark FROM contact WHERE nick_name=? OR remark=?",
                (name, name),
            ).fetchall())
        finally:
            conn.close()
    return list({r["username"]: r for r in found if str(r["username"]).endswith("@chatroom")}.values())


def self_info(db):
    for rel, path, _ in db._db_files:
        if Path(path).name != "contact.db":
            continue
        conn = db._open(rel)
        try:
            rows = conn.execute(
                "SELECT username,nick_name,remark FROM contact WHERE username=?", (db.wxid,),
            ).fetchall()
        finally:
            conn.close()
        if len(rows) == 1:
            return dict(rows[0])
    raise ValueError("无法唯一核验读取账号身份")


def restricted_rows(core, db, username, start_ts, end_ts, stats):
    if not re.fullmatch(r"[A-Za-z0-9_-]+@chatroom", username):
        raise ValueError("群稳定标识无效")
    table = "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()
    rows = []
    wanted = ["local_id", "local_type", "server_id", "real_sender_id", "create_time",
              "message_content", "source", "compress_content", "packed_info_data", "sort_seq"]
    checked = []
    for rel in db._message_dbs():
        conn = db._open(rel)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
            ).fetchone()
            if not exists:
                checked.append({"source_db": rel, "target_table": False, "selected_count": 0})
                continue
            cols = core.table_columns(conn, table)
            if not {"create_time", "local_id", "local_type"}.issubset(cols):
                raise ValueError("消息表字段不满足日期范围核查")
            selected = [c for c in wanted if c in cols]
            sender_index, map_status = core._sender_map(conn)
            # 原始值可能为秒或毫秒；两段参数化谓词均排除窗口外消息正文。
            cur = conn.execute(
                "SELECT " + ", ".join(selected) + f" FROM {table} WHERE "
                "(create_time >= ? AND create_time < ?) OR (create_time >= ? AND create_time < ?)",
                (start_ts, end_ts, start_ts * 1000, end_ts * 1000),
            )
            names = [d[0] for d in cur.description]
            selected_count = 0
            for raw in cur.fetchall():
                item = dict(zip(names, raw))
                for c in wanted:
                    item.setdefault(c, None)
                stamp = core._normalize_timestamp(item["create_time"])
                if not start_ts <= stamp < end_ts:
                    raise ValueError("消息超出授权时间范围")
                item["_db_rel"] = rel
                sid = core._sender_id(item["real_sender_id"])
                sender = core._identity_username(sender_index.get(sid))
                if sid is None:
                    status = "invalid_sender_id"
                elif map_status != "resolved":
                    status = map_status
                elif sid not in sender_index:
                    status = "unmapped_sender_id"
                elif sender is None:
                    status = "invalid_sender_username"
                else:
                    status = "resolved"
                item["_sender_username"] = sender
                item["_sender_status"] = status
                rows.append(item)
                selected_count += 1
            checked.append({"source_db": rel, "target_table": True, "selected_count": selected_count})
        finally:
            conn.close()
    if not any(s["target_table"] for s in checked):
        raise ValueError("未找到此群消息表，不能视为本期零消息")
    rows.sort(key=lambda r: (r.get("sort_seq") or 0, r.get("local_id") or 0))
    stats.update({"shards": checked, "selected_count": len(rows), "sql_date_filter": True})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["probe", "export"], required=True)
    parser.add_argument("--start", default="2026-10-06")
    parser.add_argument("--end-exclusive", default="2026-10-13")
    parser.add_argument("--out", required=True)
    parser.add_argument("--group-indices", default="1")
    parser.add_argument("--media", action="store_true")
    parser.add_argument("--transcribe-voices", action="store_true")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if WORKSPACE not in out.parents:
        raise ValueError("输出必须在当前生产管理工作区内")
    out.mkdir(parents=True, exist_ok=True)
    indices = [int(v) for v in args.group_indices.split(",")]
    if len(indices) != len(set(indices)) or any(i < 1 or i > len(GROUPS) for i in indices):
        raise ValueError("群索引不在授权清单内或重复")
    now = datetime.now(TZ)
    start = datetime.fromisoformat(args.start).replace(tzinfo=TZ)
    end = min(datetime.fromisoformat(args.end_exclusive).replace(tzinfo=TZ), now)
    if start >= end:
        raise ValueError("时间范围无效")
    account = json.loads(PRIOR_ACCOUNT_BINDING.read_text(encoding="utf-8-sig"))
    root = Path(account["db_dir"]).resolve()
    selected = (root / account["account"]).resolve()
    if selected.parent != root or not (selected / "db_storage").is_dir():
        raise ValueError("已知账号数据目录不可用")
    sys.path.insert(0, str(SOURCE))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import exporter_core as core
    manifest = {"collector_version": VERSION, "phase": args.phase,
                "started_at": now.isoformat(timespec="seconds"), "timezone": "Asia/Shanghai",
                "start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
                "account_sha256": digest(account["account"]), "sources": [],
                "queried_group_messages": args.phase == "export", "status": "running"}
    workdir = core._create_sensitive_workdir()
    sink = io.StringIO()
    db = None
    originals = (core.WeChatDB, core.find_contact, core.load_rows, core.fmt_time)
    def progress(message):
        # 第三方诊断不输出，仅打印经替换的导出进度。
        text = str(message)
        for value in (account["db_dir"], account["account"], account["self_username"]):
            text = text.replace(value, "[本地绑定]")
        text = re.sub(r"[A-Za-z0-9_-]+@chatroom", "[群标识]", text)
        print(text, flush=True, file=sys.__stdout__)
    try:
        print("正在核验授权群与本地账号…", flush=True)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            db = originals[0](db_dir=str(root), account=account["account"], workdir=str(workdir))
            db.get_self_info = types.MethodType(lambda obj: self_info(obj), db)
            info = db.get_self_info()
            if db.account != account["account"] or info["username"] != account["self_username"]:
                raise ValueError("读取账号与已知授权来源身份不一致")
            sources = []
            for i, name in enumerate(GROUPS, 1):
                found = group_rows(db, name)
                src = {"source_id": f"production_{i:02}", "index": i, "group": name,
                       "exact_match_count": len(found), "binding_verified": len(found) == 1}
                if len(found) == 1:
                    src["group_id_sha256"] = digest(found[0]["username"])
                sources.append((src, found))
            manifest["sources"] = [src for src, _ in sources]
            if args.phase == "probe":
                manifest["status"] = "metadata_verified" if all(s["binding_verified"] for s, _ in sources) else "metadata_partial"
            else:
                results = []
                for i in indices:
                    src, found = sources[i - 1]
                    if not src["binding_verified"]:
                        results.append({**src, "status": "unresolved_group"})
                        continue
                    target = found[0]
                    username = target["username"]
                    stats = {}
                    core.WeChatDB = lambda **kwargs: db
                    core.find_contact = lambda current_db, keyword: target if current_db is db and keyword == src["group"] else (_ for _ in ()).throw(ValueError("群范围不符"))
                    core.load_rows = lambda current_db, stable: restricted_rows(core, current_db, stable, int(start.timestamp()), int(end.timestamp()), stats) if current_db is db and stable == username else (_ for _ in ()).throw(ValueError("群范围不符"))
                    core.fmt_time = lambda ts: datetime.fromtimestamp(core._normalize_timestamp(ts), TZ).strftime("%Y-%m-%d %H:%M:%S")
                    progress("开始：" + src["group"])
                    group_out = out / src["source_id"]
                    try:
                        result = core._export_chat_impl(
                            src["group"], out_root=group_out, db_dir=str(root), workdir=str(workdir),
                            progress=progress, export_images=args.media, export_files=args.media,
                            export_voices=args.media, export_videos=args.media,
                            transcribe_voices=args.transcribe_voices,
                        )
                        json_path = group_out / core.safe_folder_name(target.get("remark") or target.get("nick_name") or src["group"]) / "chat_full_parsed.json"
                        data = json.loads(json_path.read_text(encoding="utf-8-sig"))
                        if data["chat_name"] != src["group"] or data["message_count"] != stats["selected_count"]:
                            raise ValueError("导出群名或数量核对不符")
                        for message in data["messages"]:
                            when = datetime.fromisoformat(message["time"]).replace(tzinfo=TZ)
                            if not start <= when < end:
                                raise ValueError("导出结果超出授权日期")
                        results.append({**src, "status": "exported", "message_count": data["message_count"],
                                        "type_counts": data["type_counts"], "media_stats": data["media_stats"],
                                        "sender_resolution_counts": data["sender_resolution_counts"],
                                        "source_json": str(json_path), "source_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
                                        "query_audit": stats})
                    except Exception as exc:
                        if stats.get("selected_count") == 0 and stats.get("sql_date_filter"):
                            results.append({**src, "status": "verified_empty_range", "message_count": 0, "query_audit": stats})
                        else:
                            results.append({**src, "status": "export_failed", "error_class": type(exc).__name__,
                                            "error": str(exc).replace(str(root), "[本地目录]").replace(account["account"], "[本地绑定]"),
                                            "query_audit": stats})
                    manifest["export_results"] = results
                    save(out / "collection_manifest_V0.1.0.json", manifest)
                manifest["status"] = "exported" if all(r["status"] in {"exported", "verified_empty_range"} for r in results) else "partial"
    except Exception as exc:
        manifest.update({"status": "failed", "error_class": type(exc).__name__, "error": str(exc).replace(str(root), "[本地目录]").replace(account["account"], "[本地绑定]")})
    finally:
        core.WeChatDB, core.find_contact, core.load_rows, core.fmt_time = originals
        db = None
        gc.collect()
        cleanup_ok, _ = core._cleanup_sensitive_workdir(workdir)
        sink.close()
        manifest["sensitive_cache_cleanup"] = cleanup_ok
        manifest["finished_at"] = datetime.now(TZ).isoformat(timespec="seconds")
        save(out / "collection_manifest_V0.1.0.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0 if manifest["status"] in {"metadata_verified", "exported"} and manifest["sensitive_cache_cleanup"] else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
