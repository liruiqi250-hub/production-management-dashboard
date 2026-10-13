"""跟进负责人的来源依据；不将跟进人推定成实际执行人。"""
from __future__ import annotations

from app.store import now

LABELS = {"assigned": "原文明示分派·待复核", "inferred": "消息推定·待确认", "manual": "人工已确认",
          "manual_legacy": "既有人工登记", "legacy": "原登记·依据待补", "unassigned": "待判断"}
ACKS = {"好", "好的", "收到", "ok", "安排", "已阅", "查看", "[视频]", "[图片]", "[文件]"}
RULE = ("owner表示跟进负责人，不是实际绘图/制作/维修人。人工指定由系统保留；原文明示交办优先，"
        "否则主动提出、催办或持续推动事项的发言人默认推定为跟进负责人。不要仅因收到/好/安排、"
        "单独媒体、转发或明显代问就指定；多人均主动且无法分清时owner=null并说明歧义。"
        "owner_basis={kind:assigned或inferred或unassigned,reason:简短判断理由,locators:[本事项证据定位]}。"
        "inferred的owner必须与所引原消息sender原样相同，assigned须有包含该姓名的明确交办原文；"
        "不输出manual类型，不虚构置信百分数。无依据时owner=null、kind=unassigned、locators=[]；存在多人歧义时可引用真实歧义消息。")


def source_evidence(item):
    return [{**e, "locator": e.get("locator") or str(e.get("source_id", "")) + "|" + str(e.get("source_ref", ""))}
            for e in item.get("evidence", [])]


def verify_owner(item):
    """消息已由调用者回填真实元数据。自由判断的语义仍需人工复核。"""
    basis = item.get("owner_basis")
    if basis is None:  # 兼容旧快照，不制造旧负责人依据。
        return
    owner = item.get("owner")
    if not isinstance(basis, dict) or basis.get("kind") not in {"assigned", "inferred", "unassigned"}:
        raise ValueError("自动负责人依据类型无效")
    reason, locators = basis.get("reason"), basis.get("locators")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500 or not isinstance(locators, list) or any(not isinstance(x, str) for x in locators):
        raise ValueError("负责人判断需简短理由及来源定位")
    if owner is not None and (not isinstance(owner, str) or not owner.strip() or len(owner) > 80):
        raise ValueError("负责人姓名无效")
    if basis["kind"] == "unassigned" and owner:
        raise ValueError("待判断负责人不能带已指定姓名")
    if basis["kind"] == "unassigned" and not locators:
        evidence = []
    else:
        indexed = {e["locator"]: e for e in source_evidence(item)}
        if (not owner and basis["kind"] != "unassigned") or not locators or any(x not in indexed for x in locators):
            raise ValueError("负责人依据不属于本事项原消息")
        evidence = [indexed[x] for x in dict.fromkeys(locators)]
        if any(not e.get("sender") or not e.get("time") or not e.get("text") for e in evidence):
            raise ValueError("负责人来源缺少真实发言人、时间或原文")
        meaningful = [e for e in evidence if e["text"].strip().lower().strip("。！!~～ ") not in ACKS]
        if not meaningful:
            raise ValueError("单纯回应或媒体占位不能判断负责人")
        if basis["kind"] == "inferred" and not any(e["sender"] == owner for e in meaningful):
            raise ValueError("推定跟进人必须是所引消息真实发言人")
        if basis["kind"] == "assigned" and not any(owner in e["text"] for e in meaningful):
            raise ValueError("明确分派依据需包含被分派人姓名")
    item["owner_basis"] = {"kind": basis["kind"], "reason": reason.strip(), "locators": list(dict.fromkeys(locators)),
                           "evidence": evidence, "checked_at": now()}


def decorate_owner(item, overrides=None):
    overrides = overrides or {}
    basis = item.get("owner_basis")
    if "owner" in overrides and "owner_basis" not in overrides:
        basis = {"kind": "manual_legacy", "reason": "沿用既有人工保存的负责人；旧记录未单独区分修改与同值保存，可重新确认。", "evidence": []}
    if not isinstance(basis, dict):
        basis = {"kind": "legacy" if item.get("owner") else "unassigned", "reason": "旧记录未单独保存负责人判断依据。", "evidence": []}
    item["owner_basis"] = {**basis, "label": LABELS.get(basis.get("kind"), "依据待核"),
                           "confirmed": basis.get("kind") == "manual"}
    return item


def preserve_assignment(previous, incoming):
    """新的主动发言推定不能取代已有明示分派。"""
    if (previous.get("owner_basis") or {}).get("kind") == "assigned" and (incoming.get("owner_basis") or {}).get("kind") in {"inferred", "unassigned"}:
        incoming["owner"] = previous.get("owner")
        incoming["owner_basis"] = previous["owner_basis"]


def manual_owner(base, overrides, changes, actor):
    """仅改变姓名或专门确认才产生人工依据；同值保存不冻结AI候选。"""
    confirmation = changes.pop("owner_confirmation", None)
    if confirmation not in {None, "confirmed"}:
        raise ValueError("负责人确认请求无效")
    current = overrides.get("owner", base.get("owner")) or None
    value = changes.get("owner", current)
    value = (value or "").strip() or None
    if confirmation and not value:
        raise ValueError("请先填写需要确认的跟进负责人")
    if value != current or confirmation:
        prior_basis = overrides.get("owner_basis", base.get("owner_basis")) or {}
        changes["owner"] = value
        changes["owner_basis"] = {"kind": "manual", "reason": ("人工确认跟进负责人为" + value) if value else "人工清空跟进负责人，等待重新分派。",
                                  "username": actor["username"], "name": actor["display_name"], "checked_at": now(), "evidence": []}
        if value and value == current and isinstance(prior_basis, dict):
            changes["owner_basis"]["evidence"] = prior_basis.get("evidence", [])
            changes["owner_basis"]["source_reason"] = prior_basis.get("source_reason") or prior_basis.get("reason", "")
    else:
        changes.pop("owner", None)


def owner_description(item, detail=False):
    basis = decorate_owner(dict(item))["owner_basis"]
    text = basis["label"] + ("：" + basis.get("reason", "") if detail else "")
    if detail:
        if basis.get("source_reason"):
            text += "\n原判断理由：" + basis["source_reason"]
        if basis.get("checked_at"):
            text += "\n记录时间：" + basis["checked_at"] + ("；确认人：" + basis["name"] if basis.get("name") else "")
        text += "\n" + "\n".join(" | ".join(str(e.get(k) or "") for k in ("locator", "time", "sender", "text")) for e in basis.get("evidence", []))
    return text.strip()
