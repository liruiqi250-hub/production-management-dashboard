"""只读核验每日采集，生成覆盖快照及待人工整理的真实消息增量。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from app.normalize import CHINA_TIME, MEDIA_FIELDS, SOURCE_GROUPS, VERSION, validate_snapshot


KIND_PREFIX = {"image": "images", "voice": "voices", "video": "videos", "file": "files"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取JSON：{path}：{error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON根节点必须是对象：{path}")
    return value


def parse_time(value: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"日期时间无效：{field}={value}") from error
    return (result.replace(tzinfo=CHINA_TIME) if result.tzinfo is None else result).astimezone(CHINA_TIME)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_existing_metadata(workspace: Path) -> tuple[dict[str, Any], str]:
    """直接以SQLite只读模式读取Store元数据，避免Store初始化造成任何写入。"""
    path = workspace / "private/production_V0.2.0.sqlite3"
    if not path.is_file():
        return {}, "数据库不存在：每群本期全部消息均作为首次待整理增量"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)) as connection:
        row = connection.execute("SELECT value_json FROM metadata WHERE key='snapshot'").fetchone()
    if not row:
        return {}, "数据库没有既有采集元数据：每群本期全部消息均作为首次待整理增量"
    value = json.loads(row[0])
    if not isinstance(value, dict):
        raise ValueError("既有Store snapshot元数据不是对象")
    return value, "只读SQLite metadata，与Store.snapshot使用同一份采集元数据；未写生产数据库"


def _media_file(media: dict[str, Any], source_dir: Path, locator: str) -> dict[str, Any]:
    path_text = media.get("path")
    if not isinstance(path_text, str) or not path_text:
        raise ValueError(f"媒体标记available=true却缺少path：{locator}")
    path = (source_dir / path_text).resolve()
    if not path.is_relative_to(source_dir.resolve()):
        raise ValueError(f"媒体路径越出当前群导出目录：{locator} / {path_text}")
    if not path.is_file():
        raise ValueError(f"媒体标记available=true但文件不存在：{locator} / {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"导出媒体为空文件：{locator} / {path}")
    sha256 = file_hash(path)
    # exporter_core._write_voice_from_row的voice_sha256计算对象是原始SILK数据；
    # 解码成功后只保留WAV，因此不能拿SILK摘要与WAV摘要作相等判断。
    if media.get("voice_sha256") and media.get("format") == "silk" and media["voice_sha256"] != sha256:
        raise ValueError(f"语音文件SHA256与导出记录不符：{locator}")
    return {"locator": locator, "kind": media["kind"], "path": str(path),
            "relative_path": path_text, "sha256": sha256, "bytes": size,
            "variant": media.get("variant"), "available": True,
            "exporter_voice_source_sha256": media.get("voice_sha256"),
            "voice_source_hash_notice": "导出器字段对应原SILK；本条sha256对应实际保留文件" if media.get("voice_sha256") else None}


def build_daily_snapshot(manifest_path: Path, workspace: Path = WORKSPACE) -> tuple[dict[str, Any], dict[str, Any]]:
    """仅返回派生数据；items为空，由Store保留既有业务事项与人工覆盖值。"""
    workspace = workspace.resolve()
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    if manifest.get("status") != "exported" or manifest.get("phase") != "export":
        raise ValueError("每日manifest尚未完成导出，拒绝生成可导入覆盖快照")
    if manifest.get("timezone") != "Asia/Shanghai" or not manifest.get("queried_group_messages"):
        raise ValueError("每日manifest缺少上海时区或真实群消息查询标记")
    start = parse_time(manifest["start_inclusive"], "manifest.start_inclusive")
    cutoff = parse_time(manifest["end_exclusive"], "manifest.end_exclusive")
    if cutoff <= start:
        raise ValueError("每日manifest截止时间不晚于起始时间")
    previous, baseline_note = read_existing_metadata(workspace)
    previous_groups = {group["source_id"]: group for group in previous.get("coverage", {}).get("groups", [])}
    now = datetime.now(CHINA_TIME).isoformat(timespec="seconds")
    results = manifest.get("export_results")
    if not isinstance(results, list) or len(results) != len(SOURCE_GROUPS):
        raise ValueError("每日manifest必须提供指定14群的完整export_results")
    groups = []
    deltas = []
    source_checks = []
    media_files = []
    unavailable = []
    all_locators = set()
    seen_sources = set()
    for source in sorted(results, key=lambda value: value["source_id"]):
        source_id = source["source_id"]
        if source_id in seen_sources or SOURCE_GROUPS.get(source_id) != source.get("group"):
            raise ValueError(f"每日manifest群映射重复或不符：{source_id}")
        seen_sources.add(source_id)
        audit = source.get("query_audit") or {}
        if not source.get("binding_verified") or source.get("exact_match_count") != 1 or not audit.get("sql_date_filter"):
            raise ValueError(f"未验证唯一群绑定或SQL日期筛选：{source_id}")
        count = source.get("message_count")
        if type(count) is not int or count < 0 or audit.get("selected_count") != count:
            raise ValueError(f"清单消息数量或SQL核对数无效：{source_id}")
        previous_group = previous_groups.get(source_id) or {}
        if previous_group and previous_group.get("group") != source["group"]:
            raise ValueError(f"既有Store群映射与本次不符：{source_id}")
        previous_cutoff_text = previous_group.get("cutoff")
        previous_cutoff = parse_time(previous_cutoff_text, f"{source_id}.previous_cutoff") if previous_cutoff_text else None
        if previous_cutoff and previous_cutoff > cutoff:
            raise ValueError(f"当前采集早于既有Store截止，拒绝回退覆盖：{source_id}")
        threshold = max(start, previous_cutoff) if previous_cutoff else start
        stats = {key: (source.get("media_stats") or {}).get(key, 0) for key in MEDIA_FIELDS}
        group = {"source_id": source_id, "group": source["group"], "message_count": count,
                 "media_stats": stats, "status": source["status"], "cutoff": cutoff.isoformat()}
        groups.append(group)
        delta_group = {"source_id": source_id, "group": source["group"],
                       "previous_cutoff": previous_cutoff_text, "start_inclusive": threshold.isoformat(),
                       "cutoff": cutoff.isoformat(), "first_collection": previous_cutoff is None, "messages": []}
        deltas.append(delta_group)
        source_path_text = source.get("source_json")
        if not source_path_text:
            if count != 0 or source["status"] != "verified_empty_range" or any(stats.values()):
                raise ValueError(f"缺少source_json但不是验证过的空群：{source_id}")
            source_checks.append({"source_id": source_id, "message_count": 0, "empty_range_verified": True,
                                  "group_binding_verified": True, "sql_date_filter_verified": True})
            continue
        source_path = Path(source_path_text)
        if not source_path.is_absolute():
            source_path = manifest_path.parent / source_path
        if not source_path.resolve().is_relative_to(manifest_path.parent):
            raise ValueError(f"source_json不在当前采集运行目录：{source_id}")
        expected_hash = source.get("source_sha256")
        source_hash = file_hash(source_path)
        if not expected_hash or source_hash != expected_hash:
            raise ValueError(f"source_json SHA256与manifest不符：{source_id}")
        parsed = read_json(source_path)
        messages = parsed.get("messages")
        if not isinstance(messages, list) or len(messages) != count or parsed.get("message_count") != count:
            raise ValueError(f"source_json消息数量与manifest不符：{source_id}")
        if parsed.get("chat_name") != source["group"]:
            raise ValueError(f"source_json群名与manifest不符：{source_id}")
        requested, exported = Counter(), Counter()
        transcript_count = 0
        times = []
        for message in messages:
            time = parse_time(message.get("time"), f"{source_id}.message.time")
            if not start <= time < cutoff:
                raise ValueError(f"原消息日期越出当前七天采集范围：{source_id} / {message.get('local_id')}")
            times.append(time)
            source_db = message.get("source_db")
            local_id = message.get("local_id")
            if not isinstance(source_db, str) or not re.fullmatch(r"message[\\/]message_\d+\.db", source_db) or type(local_id) is not int:
                raise ValueError(f"原消息源库或local_id不合法：{source_id}")
            locator = f"{source_id}|{source_db}:{local_id}"
            if locator in all_locators:
                raise ValueError(f"原消息locator重复：{locator}")
            all_locators.add(locator)
            media = message.get("media")
            verified_media = None
            transcript = message.get("transcript") or (media or {}).get("transcript")
            transcript_source = message.get("transcript_source") or (media or {}).get("transcript_source")
            if media:
                kind = media.get("kind")
                if kind not in KIND_PREFIX:
                    raise ValueError(f"未知导出媒体kind：{locator} / {kind}")
                requested[kind] += 1
                if type(media.get("available")) is not bool:
                    raise ValueError(f"媒体available必须是真假值：{locator}")
                if media["available"]:
                    exported[kind] += 1
                    verified_media = _media_file(media, source_path.parent, locator)
                    media_files.append(verified_media)
                else:
                    unavailable.append({"source_id": source_id, "locator": locator, "kind": kind,
                                        "reason": media.get("error") or media.get("reason") or "本机当前采集未提供可用缓存",
                                        "available": False})
                if kind == "voice" and isinstance(transcript, str) and transcript.strip():
                    transcript_count += 1
            if time >= threshold:
                delta_group["messages"].append({
                    "source_id": source_id, "group": source["group"], "locator": locator,
                    "time": time.isoformat(), "sender": message.get("sender"), "type": message.get("type"),
                    "text": message.get("content"), "media": media, "exported_media": verified_media,
                    "transcript": transcript, "transcript_source": transcript_source,
                    "transcript_notice": "本地ASR机器转写，未核听，不当作发言人准确原话" if transcript_source == "local_asr" else None,
                })
        actual_stats = {"voice_transcripts": transcript_count}
        for kind, prefix in KIND_PREFIX.items():
            actual_stats[f"{prefix}_requested"] = requested[kind]
            actual_stats[f"{prefix}_exported"] = exported[kind]
        if stats != actual_stats:
            raise ValueError(f"原消息实际媒体数量与manifest不一致：{source_id} / {actual_stats} / {stats}")
        if {key: (parsed.get("media_stats") or {}).get(key, 0) for key in MEDIA_FIELDS} != stats:
            raise ValueError(f"source_json媒体统计与manifest不一致：{source_id}")
        if file_hash(source_path) != source_hash:
            raise ValueError(f"核查期间source_json发生变化：{source_id}")
        source_checks.append({"source_id": source_id, "source_json": str(source_path), "source_sha256": source_hash,
                              "hash_verified": True, "message_count": count, "all_dates_verified": True,
                              "first_message_time": min(times).isoformat() if times else None,
                              "last_message_time": max(times).isoformat() if times else None,
                              "group_binding_verified": True, "sql_date_filter_verified": True,
                              "actual_media_stats_verified": actual_stats})
    if seen_sources != set(SOURCE_GROUPS):
        raise ValueError("每日manifest未完整映射指定14群")
    for delta_group in deltas:
        delta_group["message_count"] = len(delta_group["messages"])
    media_totals = {key: sum(group["media_stats"][key] for group in groups) for key in MEDIA_FIELDS}
    new_count = sum(group["message_count"] for group in deltas)
    limitations = [
        f"当前覆盖为14群本机七天采集快照，截止{cutoff.isoformat()}；今天不是完整一天。",
        f"已逐条核验{len(all_locators)}条消息的源文件SHA256、群映射、日期范围、数量与媒体统计；这些技术校验不代表已逐条完成业务内容审查。",
        f"当前已实际检查{len(media_files)}个available=true媒体文件路径、非空大小及SHA256；未目视图片、观看视频、核听语音或打开本批附件内容。",
        f"图片导出{media_totals['images_exported']}/{media_totals['images_requested']}；视频导出{media_totals['videos_exported']}/{media_totals['videos_requested']}；缺本机缓存不等于没有事件或结果。",
        f"语音导出{media_totals['voices_exported']}/{media_totals['voices_requested']}，有非空转写{media_totals['voice_transcripts']}条；本地ASR未经核听，不视作准确原话。",
        f"按既有Store各群cutoff提取{new_count}条待整理增量；本快照items为空，不自动新建、关闭或覆盖既有业务事项，旧未结与人工改动由Store保留。",
        "历史18条精选事项的证据仅代表精选依据，不代表旧801条消息全部已经审阅；本次增量内容审查应另保存daily_review及确切locator。",
        "verified_empty_range只说明本机指定日期SQL查询为零，不推断群内没有问题或手机没有记录。",
    ]
    verification = {"manifest_path": str(manifest_path), "manifest_sha256": file_hash(manifest_path),
                    "source_jsons_verified": sum(bool(source.get("source_json")) for source in source_checks),
                    "message_dates_verified": len(all_locators), "available_media_paths_hashed": len(media_files),
                    "missing_media_count": len(unavailable), "business_delta_reviewed": False,
                    "production_database_access": "sqlite mode=ro", "baseline_note": baseline_note}
    snapshot = {"version": VERSION, "generated_at": now,
                "period": {"start": start.isoformat(), "end": cutoff.isoformat(), "cutoff": cutoff.isoformat(),
                           "cutoffs": [cutoff.isoformat()], "timezone": "Asia/Shanghai"},
                "coverage": {"groups": groups, "message_count": sum(group["message_count"] for group in groups),
                             "media": media_totals, "limitations": limitations, "verification": verification},
                "items": []}
    validate_snapshot(snapshot)
    delta = {"version": VERSION, "generated_at": now, "run": manifest_path.parent.name,
             "manifest_path": str(manifest_path), "manifest_sha256": verification["manifest_sha256"],
             "period": snapshot["period"], "baseline_generated_at": previous.get("generated_at"),
             "baseline_note": baseline_note, "message_count": new_count,
             "review_state": "待逐条核查本轮增量；不是全量业务已阅证明", "groups": deltas,
             "verification": {**verification, "sources": source_checks, "media_files": media_files,
                              "unavailable_media": unavailable},
             "limitations": limitations}
    return snapshot, delta


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="V0.2.0每日覆盖校验与真实消息增量准备；不写生产数据库")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest if args.manifest.is_absolute() else WORKSPACE / args.manifest
    output = args.out if args.out.is_absolute() else WORKSPACE / args.out
    snapshot, delta = build_daily_snapshot(manifest_path)
    safe_run = re.sub(r"[^A-Za-z0-9_-]", "_", manifest_path.parent.name)
    delta_path = WORKSPACE / "private" / f"daily_delta_{safe_run}.json"
    write_json(output, snapshot)
    write_json(delta_path, delta)
    print(json.dumps({"version": VERSION, "snapshot": str(output), "delta_path": str(delta_path),
                      "groups": len(snapshot["coverage"]["groups"]),
                      "messages": snapshot["coverage"]["message_count"], "delta_messages": delta["message_count"],
                      "media": snapshot["coverage"]["media"],
                      "media_paths_hashed": delta["verification"]["available_media_paths_hashed"],
                      "delta_groups": {group["source_id"]: group["message_count"] for group in delta["groups"] if group["message_count"]},
                      "database_written": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
