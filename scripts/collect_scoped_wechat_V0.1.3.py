"""本轮已授权生产群的只读日期范围采集辅助工具 V0.1.3。"""
from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import io
import json
import os
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "0.1.3"
WORKSPACE = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ["DINO_WECHAT_RUNTIME"]) if os.environ.get("DINO_WECHAT_RUNTIME") else WORKSPACE / "vendor" / "wechat"
SOURCE = RUNTIME / "source" / "wechat-chat-export-1.3.4"
PRIOR_ACCOUNT_BINDING = Path(os.environ["DINO_WECHAT_BINDING"]) if os.environ.get("DINO_WECHAT_BINDING") else WORKSPACE / "private" / "wechat_binding.json"
TZ = timezone(timedelta(hours=8))
GROUPS = [
    "生产资料群", "彩灯传动沟通群", "李婷订单沟通群", "曾霞订单沟通群",
    "刘欢订单交流群", "谢梦恐龙订单沟通群", "刘佳订单沟通交流群", "范紫薇订单沟通群",
    "呙小凤生产沟通交流群", "王雪珂订单交流群", "刘家丽订单沟通群", "龚娟订单沟通群",
    "缪如玉仿真恐龙生产沟通群", "董雨婷订单沟通群",
]


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collection_period(start_text, end_text, now):
    """仅允许含中国当天的最近七个自然日内采集，截止不超过本轮实际时间。"""
    now = now.astimezone(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = datetime.strptime(start_text, "%Y-%m-%d").replace(tzinfo=TZ) if start_text else today - timedelta(days=6)
    requested_end = datetime.strptime(end_text, "%Y-%m-%d").replace(tzinfo=TZ) if end_text else today + timedelta(days=1)
    if start < today - timedelta(days=6) or requested_end > today + timedelta(days=1):
        raise ValueError("日期超出已授权的滚动最近七天范围")
    end = min(requested_end, now)
    if start >= end:
        raise ValueError("时间范围无效")
    return start, end


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
    parser.add_argument("--start")
    parser.add_argument("--end-exclusive")
    parser.add_argument("--out", required=True)
    parser.add_argument("--group-indices", default="1")
    parser.add_argument("--media", action="store_true")
    parser.add_argument("--transcribe-voices", action="store_true")
    parser.add_argument("--scope", choices=["production", "after_sales", "lantern"], default="production")
    args = parser.parse_args()
    selected_groups = GROUPS if args.scope == "production" else ["大洋安装售后群", "云艺安装售后群", "金耐安装售后群"]
    if args.scope == "lantern":
        sys.path.insert(0, str(WORKSPACE))
        from app.lantern_sources import load_sources
        selected_groups = load_sources(WORKSPACE)["wechat_groups"]
        if not selected_groups:
            raise ValueError("彩灯来源未授权微信群")
    out = Path(args.out).resolve()
    if WORKSPACE not in out.parents:
        raise ValueError("输出必须在当前生产管理工作区内")
    if any(out.glob("collection_manifest_*.json")):
        raise ValueError("已有采集证据，请使用新的独立输出目录")
    out.mkdir(parents=True, exist_ok=True)
    indices = [int(v) for v in args.group_indices.split(",")]
    if len(indices) != len(set(indices)) or any(i < 1 or i > len(selected_groups) for i in indices):
        raise ValueError("群索引不在授权清单内或重复")
    now = datetime.now(TZ)
    start, end = collection_period(args.start, args.end_exclusive, now)
    if not (SOURCE / "exporter_core.py").is_file():
        raise ValueError("未找到微信采集组件，请按dependencies/微信采集依赖.md配置本机采集环境")
    if not PRIOR_ACCOUNT_BINDING.is_file():
        raise ValueError("未配置本机微信授权绑定，请在private/wechat_binding.json中设置已授权账号")
    account = json.loads(PRIOR_ACCOUNT_BINDING.read_text(encoding="utf-8-sig"))
    root = Path(account["db_dir"]).resolve()
    selected = (root / account["account"]).resolve()
    if selected.parent != root or not (selected / "db_storage").is_dir():
        raise ValueError("已知账号数据目录不可用")
    bundled_ffmpeg = RUNTIME / "ffmpeg" / "bin" / "ffmpeg.exe"
    if bundled_ffmpeg.is_file():
        os.environ["IMAGEIO_FFMPEG_EXE"] = str(bundled_ffmpeg)
    sys.path.insert(0, str(SOURCE))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import exporter_core as core
    bundled_model_dir = SOURCE / "tools" / "asr" / "sensevoice"
    portable_runtime = (RUNTIME / "python" / "python.exe").is_file()
    if portable_runtime and args.transcribe_voices and not all((bundled_model_dir / name).is_file() for name in ("model.int8.onnx", "tokens.txt")):
        raise ValueError("完整包中的语音模型缺失，请先运行检查完整安装.cmd")
    manifest = {"collector_version": VERSION, "phase": args.phase,
                "started_at": now.isoformat(timespec="seconds"), "timezone": "Asia/Shanghai",
                "start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
                "account_sha256": digest(account["account"]), "sources": [],
                "queried_group_messages": False, "status": "running"}
    workdir = core._create_sensitive_workdir()
    sink = io.StringIO()
    db = None
    originals = (core.WeChatDB, core.find_contact, core.load_rows, core.fmt_time, core._persistent_asr_cache_path)
    original_model_dir = core._persistent_sensevoice_dir
    if portable_runtime:
        core._persistent_sensevoice_dir = lambda: bundled_model_dir
    core._persistent_asr_cache_path = lambda: out / "private" / "voice_asr_cache_V0.1.3.json"
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
            for i, name in enumerate(selected_groups, 1):
                found = group_rows(db, name)
                source_id = f"lantern_{i:02}" if args.scope == "lantern" else f"production_{i:02}" if args.scope == "production" else ["dayang", "yunyi", "jinnai"][i - 1]
                src = {"source_id": source_id, "index": i, "group": name,
                       "exact_match_count": len(found), "binding_verified": len(found) == 1}
                if len(found) == 1:
                    src["group_id_sha256"] = digest(found[0]["username"])
                elif len(found) == 0:
                    src["candidate_group_names"] = [r.get("remark") or r.get("nick_name") for r in db.search_contact(name[:3]) if str(r.get("username", "")).endswith("@chatroom")]
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
                    expected_display = target.get("remark") or target.get("nick_name") or src["group"]
                    stats = {}
                    manifest["queried_group_messages"] = True
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
                        if data["chat_name"] != expected_display or data["message_count"] != stats["selected_count"]:
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
                    save(out / "collection_manifest_V0.1.3.json", manifest)
                manifest["status"] = "exported" if all(r["status"] in {"exported", "verified_empty_range"} for r in results) else "partial"
    except Exception as exc:
        manifest.update({"status": "failed", "error_class": type(exc).__name__, "error": str(exc).replace(str(root), "[本地目录]").replace(account["account"], "[本地绑定]")})
    finally:
        core.WeChatDB, core.find_contact, core.load_rows, core.fmt_time, core._persistent_asr_cache_path = originals
        core._persistent_sensevoice_dir = original_model_dir
        db = None
        gc.collect()
        cleanup_ok, _ = core._cleanup_sensitive_workdir(workdir)
        sink.close()
        manifest["sensitive_cache_cleanup"] = cleanup_ok
        manifest["finished_at"] = datetime.now(TZ).isoformat(timespec="seconds")
        save(out / "collection_manifest_V0.1.3.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0 if manifest["status"] in {"metadata_verified", "exported"} and manifest["sensitive_cache_cleanup"] else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
