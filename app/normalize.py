"""将只读采集清单与首轮精选候选统一为 V0.2.0 初始化快照。

本模块不会按关键词把全部聊天自动认定为问题，也不会写回采集原件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


VERSION = "0.2.0"
CHINA_TIME = timezone(timedelta(hours=8))
REVIEW_PENDING = "首轮精选待副厂复核"
CATEGORIES = frozenset({"问题", "任务", "资料", "确认"})
STATUSES = frozenset({"待确认", "待处理", "处理中", "待验证", "已解决"})
REVIEWED_STATES = frozenset({"副厂已复核", "人工已复核", "已复核", "已审核"})
SOURCE_GROUPS = {
    "production_01": "生产资料群",
    "production_02": "彩灯传动沟通群",
    "production_03": "李婷订单沟通群",
    "production_04": "曾霞订单沟通群",
    "production_05": "刘欢订单交流群",
    "production_06": "谢梦恐龙订单沟通群",
    "production_07": "刘佳订单沟通交流群",
    "production_08": "范紫薇订单沟通群",
    "production_09": "呙小凤生产沟通交流群",
    "production_10": "王雪珂订单交流群",
    "production_11": "刘家丽订单沟通群",
    "production_12": "龚娟订单沟通群",
    "production_13": "缪如玉仿真恐龙生产沟通群",
    "production_14": "董雨婷订单沟通群",
}
MEDIA_FIELDS = (
    "images_requested", "images_exported", "voices_requested", "voices_exported",
    "voice_transcripts", "videos_requested", "videos_exported",
    "files_requested", "files_exported",
)
_LOCATOR = re.compile(r"^(production_\d{2})\|message[\\/]message_\d+\.db:\d+$")

# 这里登记的是已逐条核对的阶段确认，不是通用的聊天关键词提取规则。
# 关闭时间取直接确认原消息，不能取最后一条“好”或采集时间。
def _stage_closures():
    # 实际群聊确认原文留在本机私有配置，不随代码上传。
    path = Path(__file__).resolve().parents[1] / "private/stage_closures_V0.2.0.json"
    if not path.is_file():
        return {}
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError("阶段确认私有配置需为对象")
    return value

_SAMPLE_MAP = {
    "资料已接收_参数待确认": ("资料", "待确认"),
    "处理中_待结果验证": ("问题", "处理中"),
    "确认子任务已完成": ("确认", "已解决"),
    "已明确交办_待结果": ("任务", "待处理"),
    "已答应安排调整_待验证": ("问题", "待处理"),
}
_REMAINING_MAP = {
    "需求变更与技术阻塞": ("问题", "待确认"),
    "客户确认阻塞": ("确认", "待确认"),
    "外观调整与确认阻塞": ("问题", "待处理"),
    "已确认的阶段子任务": ("确认", "已解决"),
    "声效修改待验证": ("问题", "待处理"),
    "外观修改待确认": ("问题", "待确认"),
    "已确认的颜色调整子任务": ("确认", "已解决"),
    "结构方案变更及阶段放行": ("确认", "已解决"),
}


def _fail(message: str) -> None:
    raise ValueError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取JSON：{path}：{error}") from error
    if not isinstance(value, dict):
        _fail(f"JSON根节点必须是对象：{path}")
    return value


def _parse_time(value: Any, field: str, *, allow_naive: bool = False) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field}必须是非空ISO日期时间")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field}日期时间无效：{value}") from error
    if result.tzinfo is None:
        if not allow_naive:
            _fail(f"{field}必须包含时区：{value}")
        result = result.replace(tzinfo=CHINA_TIME)
    return result


def _iso(value: str, field: str = "原始时间") -> str:
    return _parse_time(value, field, allow_naive=True).astimezone(CHINA_TIME).isoformat()


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field}必须是非空文本")
    return value


def _count(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        _fail(f"{field}必须是非负整数")
    return value


def _stable_id(original: dict[str, Any], source_id: str, locator: str) -> str:
    item_id = original.get("item_id") or original.get("id")
    if item_id:
        return _nonempty(item_id, "候选事项id")
    digest = hashlib.sha256(f"{source_id}|{locator}".encode("utf-8")).hexdigest()[:16]
    return f"{source_id}-{digest}"


def _evidence(original: dict[str, Any], source_id: str) -> dict[str, str]:
    locator = original.get("locator") or original.get("source_ref") or original.get("source_locator")
    locator = _nonempty(locator, "证据locator")
    if not locator.startswith("production_"):
        locator = f"{source_id}|{locator}"
    text = original.get("exact_content")
    if text is None:
        text = original.get("text_excerpt")
    if text is None:
        text = original.get("content")
    text = _nonempty(text, f"{locator}.text")
    if original.get("quoted_text_excerpt"):
        text += f"\n    ↳ 引用 {original['quoted_text_excerpt']}"
    return {
        "locator": locator,
        "time": _iso(original.get("time"), f"{locator}.time"),
        "sender": _nonempty(original.get("sender_display") or original.get("sender"), f"{locator}.sender"),
        "text": text,
    }


def _make_item(original: dict[str, Any], source_id: str, category: str, status: str,
               evidence: list[dict[str, str]], summary: str, next_step: str,
               *, owner: str | None = None, deadline: str | None = None,
               closure_scope: str = "尚无足够的完成或验收依据，未闭环。") -> dict[str, Any]:
    if source_id not in SOURCE_GROUPS:
        _fail(f"候选事项source_id无效：{source_id}")
    if not evidence:
        _fail(f"候选事项缺少证据：{original.get('title')}")
    item_id = _stable_id(original, source_id, evidence[0]["locator"])
    dates = [entry["time"] for entry in evidence]
    item = {
        "id": item_id,
        "title": _nonempty(original.get("title"), f"{item_id}.title"),
        "category": category,
        "status": status,
        "owner": owner,
        "deadline": deadline,
        "first_seen": min(dates, key=datetime.fromisoformat),
        "last_seen": max(dates, key=datetime.fromisoformat),
        "summary": _nonempty(summary, f"{item_id}.summary"),
        "next_step": _nonempty(next_step, f"{item_id}.next_step"),
        "source_id": source_id,
        "group": SOURCE_GROUPS[source_id],
        "evidence": evidence,
        "closure_scope": closure_scope,
        "review_state": REVIEW_PENDING,
        "closed_at": None,
    }
    if status == "已解决":
        closure = _stage_closures().get(item_id)
        if not closure:
            _fail(f"未登记直接阶段确认依据，不能关闭：{item_id}")
        item["closure_scope"] = closure["scope"]
        item["closed_at"] = closure["time"]
    return item


def _sample_items(review: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for original in review["items"]:
        source_id = original["source_id"]
        if original["status"] not in _SAMPLE_MAP:
            _fail(f"未知样本状态，需人工建立映射：{original['status']}")
        category, status = _SAMPLE_MAP[original["status"]]
        summary_parts = list(original["facts"])
        for key in ("product_or_order", "requested_timing", "owner_basis", "media_scope"):
            if original.get(key):
                summary_parts.append(original[key])
        closure = original.get("closure", {})
        scope = f"已确认范围：{closure.get('confirmed_scope', '无')}；尚未闭环。"
        if closure.get("missing_evidence"):
            scope += "缺少：" + "、".join(closure["missing_evidence"]) + "。"
        if closure.get("insufficient_for"):
            scope += "不足以证明：" + "、".join(closure["insufficient_for"]) + "。"
        result.append(_make_item(
            original, source_id, category, status,
            [_evidence(e, source_id) for e in original["evidence"]],
            "\n".join(summary_parts), original["recommended_next_step"],
            owner=original.get("owner"), deadline=original.get("deadline"), closure_scope=scope,
        ))
    return result


def _file_items(review: dict[str, Any], messages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    source_id = "production_01"
    if review.get("source_group") != SOURCE_GROUPS[source_id]:
        _fail("资料审阅文件的source_group与production_01不一致")
    files = {file["source_locator"]: file for file in review["files"]}
    result = []
    for original in review["dashboard_candidates"]:
        relative_locator = original["source_locator"]
        file = files.get(relative_locator)
        locator = f"{source_id}|{relative_locator}"
        if not file or locator not in messages:
            _fail(f"资料候选找不到对应文件或原消息：{locator}")
        message = messages[locator]
        evidence = _evidence({**message, "locator": locator}, source_id)
        cells = "、".join(original["cells"])
        summary = [f"附件《{file['file_name']}》中的资料核对候选：{original['title']}。定位：{cells}。"]
        for abnormality in file.get("confirmed_abnormalities", []):
            if set(abnormality.get("locations", [])) & set(original["cells"]):
                summary.append(abnormality["value"] + "。")
        if file.get("pending_confirmation"):
            summary.append("同文件待确认：" + "；".join(file["pending_confirmation"]))
        if file.get("responsible_people"):
            value = file["responsible_people"]
            summary.append(f"文件原文负责人：{value['value']}（{value['location']}）；仅保留文件指派文字，未确认本核对事项的owner。")
        if file.get("follow_up"):
            value = file["follow_up"]
            summary.append(f"文件原文跟单：{value['value']}（{value['location']}）。")
        summary.append(file["production_status"] + "。")
        evidence["text"] += f"\n附件核对定位：{cells}\n核对候选：{original['title']}"
        result.append(_make_item(
            original, source_id, "资料", "待确认", [evidence], "\n".join(summary),
            f"请资料提供方核对《{file['file_name']}》的{cells}，补充或确认“{original['title']}”，并登记明确的复核责任人。",
            closure_scope="仅附件内容核对候选；未获更正或确认依据，未关闭。不能据生产单发送判断开工、库存、出货或验收。",
        ))
    return result


def _remaining_items(review: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for original in review["items"]:
        source_id = original["source_id"]
        if original["category"] not in _REMAINING_MAP:
            _fail(f"未知剩余样本类别，需人工建立映射：{original['category']}")
        category, status = _REMAINING_MAP[original["category"]]
        summary = [original["order_or_product"], original["status_reason"]]
        stated_owner = original.get("overall_assigned_owner")
        # 该字段的现有非空值是“回复接受请求，执行人未明”，不是已明确的执行分工。
        if stated_owner:
            summary.append(f"原核查的沟通承接说明：{stated_owner}；具体执行人未明，本事项owner留空。")
        result.append(_make_item(
            original, source_id, category, status,
            [_evidence(e, source_id) for e in original["evidence"]],
            "\n".join(summary), original["next_attention"],
            closure_scope=f"原核查状态：{original['status']}；尚无对应范围的最终完成或认可依据，未闭环。",
        ))
    return result


def _load_sources(workspace: Path, manifests: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    groups: dict[str, dict[str, Any]] = {}
    messages: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        for source in manifest["export_results"]:
            source_id = source["source_id"]
            if source_id in groups:
                _fail(f"采集清单source_id重复：{source_id}")
            if SOURCE_GROUPS.get(source_id) != source["group"]:
                _fail(f"采集清单群映射不一致：{source_id}/{source['group']}")
            if not source.get("binding_verified") or not source.get("query_audit", {}).get("sql_date_filter"):
                _fail(f"采集清单未验证群绑定或日期筛选：{source_id}")
            stats = {key: _count((source.get("media_stats") or {}).get(key, 0), f"{source_id}.{key}") for key in MEDIA_FIELDS}
            groups[source_id] = {
                "source_id": source_id, "group": source["group"],
                "message_count": _count(source.get("message_count"), f"{source_id}.message_count"),
                "media_stats": stats, "status": source["status"],
                "cutoff": manifest["end_exclusive"],
            }
            path_value = source.get("source_json")
            if not path_value:
                if source["message_count"] != 0:
                    _fail(f"非空群缺少原消息JSON：{source_id}")
                continue
            path = Path(path_value)
            if not path.is_absolute():
                path = workspace / path
            parsed = _read_json(path)
            expected_hash = source.get("source_sha256")
            if expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                _fail(f"原消息JSON哈希与采集清单不符：{source_id}")
            if parsed.get("chat_name") != source["group"] or len(parsed.get("messages", [])) != source["message_count"]:
                _fail(f"原消息群名或数量与清单不符：{source_id}")
            for message in parsed["messages"]:
                locator = f"{source_id}|{message['source_db']}:{message['local_id']}"
                if locator in messages:
                    _fail(f"原消息locator重复：{locator}")
                messages[locator] = message
    return [groups[key] for key in sorted(groups)], messages


def build_bootstrap_snapshot(workspace: Path) -> dict[str, Any]:
    """只读构建18条首轮精选候选；不更改人工维护的正式状态文件。"""
    workspace = Path(workspace).resolve()
    manifests = [_read_json(workspace / path) for path in (
        "acquisition/V0.1.2/pilot_001/collection_manifest_V0.1.2.json",
        "acquisition/V0.1.3/batch_001/collection_manifest_V0.1.3.json",
    )]
    sample = _read_json(workspace / "analysis/sample_review_V0.1.0/样本事项核查.json")
    file_review = _read_json(workspace / "analysis/file_review_V0.1.0/file_review_V0.1.0.json")
    remaining = _read_json(workspace / "analysis/remaining_sample_review_V0.1.0/remaining_sample_review_V0.1.0.json")
    coverage_review = _read_json(workspace / "analysis/coverage_V0.1.0.json")
    groups, messages = _load_sources(workspace, manifests)
    reviewed_groups = {source["source_id"]: source for source in coverage_review["sources"]}
    for group in groups:
        reviewed = reviewed_groups.get(group["source_id"])
        if not reviewed or any(reviewed[key] != group[key] for key in ("group", "message_count", "status")):
            _fail(f"覆盖核查与采集清单不一致：{group['source_id']}")
    if len(reviewed_groups) != len(groups):
        _fail("覆盖核查群数量与采集清单不一致")
    media = {key: sum(group["media_stats"][key] for group in groups) for key in MEDIA_FIELDS}
    message_count = sum(group["message_count"] for group in groups)
    totals = coverage_review["totals"]
    checks = {"messages": message_count, "image_messages": media["images_requested"],
              "image_available": media["images_exported"], "voice_messages": media["voices_requested"],
              "voice_available": media["voices_exported"], "voice_local_nonempty_transcript": media["voice_transcripts"],
              "video_messages": media["videos_requested"], "video_available": media["videos_exported"],
              "file_messages": media["files_requested"], "file_available": media["files_exported"]}
    for key, value in checks.items():
        if totals.get(key) != value:
            _fail(f"覆盖核查总数与清单不一致：{key}，核查={totals.get(key)}，清单={value}")
    starts = {manifest["start_inclusive"] for manifest in manifests}
    if len(starts) != 1 or any(manifest["timezone"] != "Asia/Shanghai" for manifest in manifests):
        _fail("两份采集清单的起始时间或时区不一致")
    cutoffs = sorted({manifest["end_exclusive"] for manifest in manifests}, key=datetime.fromisoformat)
    limitations = [
        "事项为18条首轮精选待副厂复核的候选，未穷尽801条消息中的全部业务事项。",
        "覆盖最近7个自然日的本机采集快照；10月12日仅至各群cutoff，并非完整一天。",
        "production_01截止15:31:10，其余13群截止15:32:08；两批采集约相差58秒。",
        "图片204条中导出158张、缺46张；视频26条中导出18条、缺8条；缺缓存不等于产品或处理结果不存在。",
        "7条语音均完成本地转写，但转写未逐条人工核听；9个附件已导出，其中3个工作簿已读取，嵌入图片未审查。",
        "覆盖核查记录仅目视审查14张图片、1条视频抽取6帧，完整观看视频为0；不能把媒体文件存在当作完成或验收。",
        "6群在本机指定期间SQL查询为0条，只说明本机快照查询结果，不能推断群内没有问题或手机没有记录。",
        "每个已解决项只关闭closure_scope所述阶段；收悉、安排处理、发送视频、文件日期都不自动构成完工、发货或客户验收。",
    ]
    for label, review in (("两群样本核查", sample), ("资料附件核查", file_review), ("其余样本核查", remaining)):
        for note in review.get("limitations", []):
            labeled_note = f"{label}：{note}"
            if labeled_note not in limitations:
                limitations.append(labeled_note)
    snapshot = {
        "version": VERSION,
        "generated_at": datetime.now(CHINA_TIME).isoformat(timespec="seconds"),
        "period": {"start": next(iter(starts)), "end": cutoffs[-1], "cutoff": cutoffs[-1],
                   "cutoffs": cutoffs, "timezone": "Asia/Shanghai"},
        "coverage": {"groups": groups, "message_count": message_count, "media": media, "limitations": limitations},
        "items": _sample_items(sample) + _file_items(file_review, messages) + _remaining_items(remaining),
    }
    validate_snapshot(snapshot)
    for item in snapshot["items"]:
        for evidence in item["evidence"]:
            raw = messages.get(evidence["locator"])
            if not raw:
                _fail(f"精选证据找不到原消息：{item['id']} / {evidence['locator']}")
            if evidence["time"] != _iso(raw["time"]) or evidence["sender"] != raw["sender"]:
                _fail(f"精选证据时间或发言人与原消息不符：{item['id']} / {evidence['locator']}")
    return snapshot


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    """验证统一快照及未复核事项的关闭依据；失败时抛出中文ValueError。"""
    if not isinstance(snapshot, dict):
        _fail("snapshot必须是对象")
    if snapshot.get("version") != VERSION:
        _fail(f"snapshot.version必须为{VERSION}")
    _parse_time(snapshot.get("generated_at"), "generated_at")
    period = snapshot.get("period")
    if not isinstance(period, dict) or period.get("timezone") != "Asia/Shanghai":
        _fail("period必须含Asia/Shanghai时区")
    start = _parse_time(period.get("start"), "period.start")
    end = _parse_time(period.get("end") or period.get("cutoff"), "period.end/cutoff")
    if end <= start:
        _fail("period截止时间必须晚于起始时间")
    if period.get("cutoff") and _parse_time(period["cutoff"], "period.cutoff") != end:
        _fail("period.end与cutoff必须一致")
    coverage = snapshot.get("coverage")
    if not isinstance(coverage, dict) or not isinstance(coverage.get("groups"), list):
        _fail("coverage.groups必须是列表")
    group_map = {}
    for group in coverage["groups"]:
        if not isinstance(group, dict):
            _fail("coverage.groups中的群必须是对象")
        source_id = _nonempty(group.get("source_id"), "coverage.groups.source_id")
        if source_id in group_map:
            _fail(f"coverage群source_id重复：{source_id}")
        if SOURCE_GROUPS.get(source_id) != group.get("group"):
            _fail(f"coverage群映射无效：{source_id}/{group.get('group')}")
        _count(group.get("message_count"), f"{source_id}.message_count")
        if group.get("status") not in {"exported", "verified_empty_range"}:
            _fail(f"coverage群状态无效：{source_id}")
        if group["status"] == "verified_empty_range" and group["message_count"] != 0:
            _fail(f"空范围群message_count必须为0：{source_id}")
        if not isinstance(group.get("media_stats"), dict):
            _fail(f"{source_id}.media_stats必须是对象")
        for key in MEDIA_FIELDS:
            _count(group["media_stats"].get(key), f"{source_id}.{key}")
        if group["status"] == "verified_empty_range" and any(group["media_stats"].values()):
            _fail(f"空范围群媒体数必须为0：{source_id}")
        for kind in ("images", "voices", "videos", "files"):
            if group["media_stats"][f"{kind}_exported"] > group["media_stats"][f"{kind}_requested"]:
                _fail(f"{source_id}.{kind}导出数不能大于请求数")
        if group["media_stats"]["voice_transcripts"] > group["media_stats"]["voices_exported"]:
            _fail(f"{source_id}语音转写数不能大于导出语音数")
        if group.get("cutoff"):
            cutoff = _parse_time(group["cutoff"], f"{source_id}.cutoff")
            if not start < cutoff <= end:
                _fail(f"群cutoff不在快照范围：{source_id}")
        group_map[source_id] = group
    if set(group_map) != set(SOURCE_GROUPS):
        _fail("coverage必须完整且唯一映射production_01至production_14共14群")
    total = _count(coverage.get("message_count"), "coverage.message_count")
    if total != sum(group["message_count"] for group in group_map.values()):
        _fail("coverage.message_count与14群合计不一致")
    if not isinstance(coverage.get("media"), dict):
        _fail("coverage.media必须是对象")
    for key in MEDIA_FIELDS:
        total = _count(coverage["media"].get(key), f"coverage.media.{key}")
        if total != sum(group["media_stats"][key] for group in group_map.values()):
            _fail(f"coverage.media.{key}与14群合计不一致")
    for kind in ("images", "voices", "videos", "files"):
        if coverage["media"][f"{kind}_exported"] > coverage["media"][f"{kind}_requested"]:
            _fail(f"{kind}导出数不能大于请求数")
    if coverage["media"]["voice_transcripts"] > coverage["media"]["voices_exported"]:
        _fail("语音转写数不能大于导出语音数")
    if not isinstance(coverage.get("limitations"), list) or not coverage["limitations"]:
        _fail("coverage.limitations必须是非空列表")
    for note in coverage["limitations"]:
        _nonempty(note, "coverage.limitations条目")
    items = snapshot.get("items")
    if not isinstance(items, list):
        _fail("items必须是列表")
    ids = set()
    for item in items:
        if not isinstance(item, dict):
            _fail("item必须是对象")
        required = {"id", "title", "category", "status", "owner", "deadline", "first_seen", "last_seen",
                    "summary", "next_step", "source_id", "group", "evidence", "closure_scope", "review_state", "closed_at"}
        missing = required - item.keys()
        if missing:
            _fail(f"item缺少字段：{'、'.join(sorted(missing))}")
        item_id = _nonempty(item.get("id"), "item.id")
        if item_id in ids:
            _fail(f"item.id重复：{item_id}")
        ids.add(item_id)
        for key in ("title", "summary", "next_step", "closure_scope", "review_state"):
            _nonempty(item.get(key), f"{item_id}.{key}")
        if _nonempty(item.get("category"), f"{item_id}.category") not in CATEGORIES:
            _fail(f"{item_id}.category无效：{item.get('category')}")
        if _nonempty(item.get("status"), f"{item_id}.status") not in STATUSES:
            _fail(f"{item_id}.status无效：{item.get('status')}")
        if item.get("owner") is not None:
            _nonempty(item["owner"], f"{item_id}.owner")
        if item.get("deadline") is not None:
            _parse_time(item["deadline"], f"{item_id}.deadline", allow_naive=True)
        source_id = _nonempty(item.get("source_id"), f"{item_id}.source_id")
        if source_id not in group_map or item.get("group") != group_map[source_id]["group"]:
            _fail(f"{item_id}的source_id/group映射无效")
        first = _parse_time(item.get("first_seen"), f"{item_id}.first_seen")
        last = _parse_time(item.get("last_seen"), f"{item_id}.last_seen")
        if not start <= first <= last < end:
            _fail(f"{item_id}的first_seen/last_seen不在采集期间或前后倒置")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            _fail(f"{item_id}.evidence必须含至少一条证据")
        evidence_times = []
        seen_locators = set()
        for entry in evidence:
            if not isinstance(entry, dict):
                _fail(f"{item_id}.evidence证据必须是对象")
            locator = _nonempty(entry.get("locator"), f"{item_id}.evidence.locator")
            match = _LOCATOR.fullmatch(locator)
            if not match or match.group(1) != source_id:
                _fail(f"{item_id}证据locator无效或跨群：{locator}")
            if locator in seen_locators:
                _fail(f"{item_id}证据locator重复：{locator}")
            seen_locators.add(locator)
            time = _parse_time(entry.get("time"), f"{locator}.time")
            if not first <= time <= last:
                _fail(f"{item_id}证据时间超出first_seen/last_seen：{locator}")
            group_cutoff = _parse_time(group_map[source_id].get("cutoff") or period.get("end") or period.get("cutoff"), f"{source_id}.cutoff")
            if time >= group_cutoff:
                _fail(f"{item_id}证据时间不早于该群cutoff：{locator}")
            evidence_times.append(time)
            _nonempty(entry.get("sender"), f"{locator}.sender")
            _nonempty(entry.get("text"), f"{locator}.text")
        if first != min(evidence_times) or last != max(evidence_times):
            _fail(f"{item_id}.first_seen/last_seen必须对应证据最早/最晚时间")
        if item["status"] != "已解决":
            if item.get("closed_at") is not None:
                _fail(f"{item_id}未解决时closed_at必须为空")
            continue
        closed_at = _parse_time(item.get("closed_at"), f"{item_id}.closed_at")
        closure_evidence = [entry for entry in evidence if _parse_time(entry["time"], "证据时间") == closed_at]
        if not closure_evidence:
            _fail(f"{item_id}.closed_at必须对应直接确认消息时间，不能使用采集或最后回应时间")
        if item["review_state"] not in REVIEWED_STATES:
            registered = _stage_closures().get(item_id)
            if not registered:
                _fail(f"{item_id}尚未副厂复核且未登记阶段确认依据，不能关闭")
            if item["closure_scope"] != registered["scope"] or item["closed_at"] != registered["time"]:
                _fail(f"{item_id}未复核关闭范围或时间与已核对阶段依据不一致")
            if not any(entry["locator"] == registered["locator"] and entry["text"] == registered["text"] for entry in closure_evidence):
                _fail(f"{item_id}缺少已核对的直接阶段确认消息")
        elif all(entry["text"].strip() in {"好", "好的", "收到", "嗯", "OK", "ok", "[图片]", "[视频]"} for entry in closure_evidence):
            _fail(f"{item_id}单纯回应或发送媒体不能作为关闭依据")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成V0.2.0首轮精选生产管理初始化快照")
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    snapshot = build_bootstrap_snapshot(args.workspace)
    target = args.out if args.out.is_absolute() else args.workspace / args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {target}：{len(snapshot['items'])} 条精选候选，{snapshot['coverage']['message_count']} 条消息 / 14 群")


if __name__ == "__main__":
    main()
