"""经本机管理员持续授权的生产阶段复核；不替代人工质量验收。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from datetime import date, datetime

from app.store import now, TZ

STAGES = ("机械图", "钢架", "造型", "纹理", "烫纹", "表皮", "刷胶", "上色", "调整", "方案选择", "资料交付", "拆卸", "打包", "出货")
BASES = ("业务阶段放行", "现场完成反馈", "后续工序已发生", "答复或选择完成")
FIELDS = {"entity", "stage", "basis", "locator", "context_locators", "remaining_item_ids", "reason"}
FLOW = (("机械图", r"钢架|钳工"), ("钢架", r"包海绵|造型"), ("造型", r"纹理|纹路|烫纹|粘袜子|喷色|上色"),
        ("纹理", r"粘袜子|刷胶|喷色|上色"), ("烫纹", r"粘袜子|刷胶|喷色|上色"), ("表皮", r"刷胶|喷色|上色"),
        ("刷胶", r"喷色|上色"), ("拆卸", r"打包|装箱|出货"), ("打包", r"出货|装车完成"))
ACKS = {"好", "好的", "收到", "嗯", "嗯嗯", "ok", "[ok]", "安排", "[图片]", "[视频]"}
FUTURE = re.compile(r"准备|明天|后天|等.{0,12}(?:确认|回复|干了)|尽量|估计|还没|尚未|没有.{0,8}(?:做|改|完成)|没(?:改|做)|(?:喊|叫|安排)师傅")
FINAL = re.compile(r"最终验收|客户验收|质量合格|整单完工|整单完成|全部产品完成")


def load_policy(workspace, store):
    path = Path(workspace) / "private/context_review_policy.json"
    if not path.is_file():
        return {"enabled": False, "scope": "production_stage_only"}
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ValueError
        value = json.loads(path.read_text("utf-8-sig"))
        if (not isinstance(value, dict) or set(value) != {"enabled", "scope", "authorized_by", "authorized_on"}
                or type(value["enabled"]) is not bool or value["scope"] != "production_stage_only"
                or not isinstance(value["authorized_by"], str) or date.fromisoformat(value["authorized_on"]) > datetime.now(TZ).date()):
            raise ValueError
        with store.connect() as connection:
            actor = connection.execute("SELECT role,active FROM users WHERE username=?", (value["authorized_by"],)).fetchone()
        if not actor or actor["role"] != "admin" or not actor["active"]:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError("本机自动阶段复核授权配置无效，正式台账保留") from None


def _body(evidence):
    return str(evidence.get("text") or "").split("↳ 引用", 1)[0].strip()


def _claims_final(scope):
    for clause in re.split(r"[，,。；;]|但是|然而|但", scope):
        for match in FINAL.finditer(clause):
            if not re.search(r"不(?:代表|含|包括|覆盖|等于|是|作)|不是|并非|尚未|未通过|不能", clause[:match.start()]):
                return True
    return False


def prepare_resolutions(items, messages, office, policy):
    """候选证据已由verify_candidates回填；这里再次约束自动关闭范围。"""
    from app.office import canonical_name
    by_id = {item["id"]: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("阶段复核事项ID重复")
    plans = []
    for item in items:
        resolution = item.get("stage_resolution")
        if resolution is None:
            continue
        if not policy.get("enabled"):
            raise ValueError("本机未启用自动阶段完成授权")
        if (not isinstance(resolution, dict) or set(resolution) != FIELDS
                or resolution["stage"] not in STAGES or resolution["basis"] not in BASES
                or any(not isinstance(resolution[k], str) or not resolution[k].strip() or len(resolution[k]) > 1000 for k in ["entity", "reason", "locator"])
                or any(not isinstance(resolution[k], list) or len(resolution[k]) > 20 or any(not isinstance(x, str) for x in resolution[k]) for k in ["context_locators", "remaining_item_ids"])):
            raise ValueError("阶段完成方案结构无效")
        evidence = {e["locator"]: e for e in item.get("evidence", [])}
        anchor = evidence.get(resolution["locator"])
        refs = list(dict.fromkeys([resolution["locator"], *resolution["context_locators"]]))
        if (not anchor or not resolution["context_locators"] or len(refs) < 2
                or any(ref not in evidence or ref not in messages or ref.split("|")[0] != item["source_id"] for ref in refs)):
            raise ValueError("阶段完成必须有同一事项的本轮原消息及上下文")
        raw_anchor = messages[resolution["locator"]]
        # 微信文字引用回复为49/57卡片；仍有直接输入的文字，不等同媒体或文件。
        quoted_text = (raw_anchor.get("type_code") == (57 << 32 | 49)
                       and "↳ 引用" in str(raw_anchor.get("content") or ""))
        if str(raw_anchor.get("type")) != "文本" and not quoted_text:
            raise ValueError("阶段完成关键依据必须为明确文字，媒体或未核听语音不自动关闭")
        body = _body(anchor)
        scope = item.get("closure_scope") or ""
        if (not scope.strip() or _claims_final(scope) or FINAL.search(resolution["stage"])
                or body.lower() in ACKS and resolution["basis"] != "业务阶段放行"):
            raise ValueError("自动复核只关闭有明确依据的阶段，不能冒充最终验收")
        basis = resolution["basis"]
        if basis == "业务阶段放行":
            confirmation = item.get("stage_confirmation") or {}
            if confirmation.get("locator") != anchor["locator"] or confirmation.get("time") != anchor["time"]:
                raise ValueError("阶段放行需对应已核验的阶段确认消息")
        elif basis == "答复或选择完成":
            if resolution["stage"] not in {"方案选择", "资料交付"} or not re.search(r"答复|澄清|选择|选定|资料|参数|预计日期", scope):
                raise ValueError("答复完成只能限于本次信息澄清或方案选择")
            if (body.lower() in ACKS or re.match(r"^(?:好的[,， ]*)?(?:谢谢|感谢|收到)", body)
                    or FUTURE.search(body) or not any(re.search(r"请问|需不需要|需要.{0,20}不|什么|怎么|合不合适|是否|是不是|好多号|大概.{0,15}时间|[吗？?]", _body(evidence[ref])) for ref in resolution["context_locators"])):
                raise ValueError("答复完成需直接回答内容及具体问题，收悉或未来答复不算")
        else:
            if canonical_name(anchor["sender"], office) is None:
                raise ValueError("自动现场阶段完成需办公室人员的实际进展反馈")
            if basis == "现场完成反馈":
                completion = item.get("work_completion") or {}
                if completion.get("locator") != anchor["locator"] or completion.get("time") != anchor["time"]:
                    raise ValueError("阶段完成需对应已核验的现场完成反馈")
                if not re.search(r"已|好了|做完|完成|调整后|修改后|改了|喷完|烫了|拆了|配的", body):
                    raise ValueError("现场完成依据未说明实际处理已发生")
            else:
                pattern = dict(FLOW).get(resolution["stage"])
                if not pattern or not re.search(pattern, body) or re.search(r"参考|效果图|方案|(?:是否|可以|要不要|准备|明天|后天).{0,12}(?:纹理|喷色|上色|包海绵|粘袜子|打包|出货)", body):
                    raise ValueError("后工序依据须明确已经发生，不以参考或计划推断转序")
            # 混合句允许已完成部分后面还有计划，但完成标记必须早于未来计划。
            future = FUTURE.search(body)
            done = re.search(r"已.{0,8}(?:完成|调整)|好了|做完|完成|调整后|修改后|改了|喷完|烫了|拆了|配的", body)
            if future and (not done or future.start() < done.start()):
                raise ValueError("未来计划、估计或否定不能作为阶段完成依据")
        for remaining_id in resolution["remaining_item_ids"]:
            other = by_id.get(remaining_id)
            if (remaining_id == item["id"] or not other or other.get("source_id") != item["source_id"]
                    or other.get("stage_resolution") is not None or other.get("status") == "已解决"):
                raise ValueError("阶段后的剩余事项必须在同轮同群清单中真实保留")
        plans.append({"item_id": item["id"], **resolution, "scope": scope, "source_time": anchor["time"],
                      "source_text": anchor["text"], "evidence_sha256": hashlib.sha256(json.dumps([evidence[r] for r in refs], ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
    return plans


def apply_resolutions(store, plans, policy, connection):
    """在生产/售后导入的同一事务内写入自动结果；人工状态优先。"""
    if plans and not policy.get("enabled"):
        raise ValueError("未获自动阶段完成授权")
    result = {"closed": [], "skipped_manual": [], "already_closed": []}
    stamp = now()
    for plan in plans:
        row = connection.execute("SELECT base_json,override_json FROM items WHERE id=?", (plan["item_id"],)).fetchone()
        if not row:
            raise ValueError("阶段完成事项未导入")
        base, over = json.loads(row["base_json"]), json.loads(row["override_json"])
        auto_time = (over.get("context_review") or {}).get("reviewed_at", "")
        manual_time = over.get("manual_updated_at") or ""
        if "status" in over and (over.get("completion_source") != "context_review" or manual_time and manual_time >= auto_time):
            result["skipped_manual"].append(plan["item_id"])
            continue
        if over.get("status", base.get("status")) == "已解决":
            result["already_closed"].append(plan["item_id"])
            continue
        for remaining_id in plan["remaining_item_ids"]:
            remaining = connection.execute("SELECT base_json,override_json FROM items WHERE id=?", (remaining_id,)).fetchone()
            if not remaining:
                raise ValueError("阶段的后续事项未真实导入")
            remaining_base, remaining_override = json.loads(remaining["base_json"]), json.loads(remaining["override_json"])
            if remaining_override.get("status", remaining_base.get("status")) == "已解决":
                raise ValueError("后续工作引用了既有已完成事项，须单列真实未结工作")
        over.update(status="已解决", closed_at=plan["source_time"], closure_scope=plan["scope"],
                    review_state="Codex按持续授权复核：阶段完成", completion_source="context_review",
                    completed_by="codex", completed_by_name="Codex自动复核", context_review={**plan, "reviewed_at": stamp, "authorized_by": policy["authorized_by"]})
        connection.execute("UPDATE items SET override_json=? WHERE id=?", (json.dumps(over, ensure_ascii=False), plan["item_id"]))
        connection.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                           (stamp, "system:context-review", "context_stage_completed", plan["item_id"], json.dumps({"executor": "Codex", "policy": policy, **plan}, ensure_ascii=False)))
        result["closed"].append(plan["item_id"])
    return result
