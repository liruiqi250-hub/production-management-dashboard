"""跟进负责人的来源依据；不将跟进人推定成实际执行人。"""
from __future__ import annotations

from app.store import now

LABELS = {"assigned": "原文明示分派·待复核", "inferred": "消息推定·待确认", "manual": "人工已确认",
          "manual_legacy": "既有人工登记", "legacy": "原登记·依据待补", "unassigned": "待判断"}
ACKS = {"好", "好的", "收到", "ok", "安排", "已阅", "查看", "[视频]", "[图片]", "[文件]"}
RULE = ("owner必须从OFFICE.names办公室名单中选择，输出规范中文姓名；不是所有主动发言人都有资格。"
        "业务员、客户、安装人员及其他名单外人员只能作为需求或沟通来源，绝不能成为跟进负责人。"
        "owner表示办公室跟进负责人，不是实际绘图/制作/维修人。人工指定由系统保留；明确交给办公室成员的交办优先，"
        "否则仅可根据办公室成员主动承接、协调或持续推动的消息推定。外部人员催问不能推出内部具体某人负责；"
        "没有办公室人员承接依据时owner=null，不能用外部发言人顶替，也不能默认全部分给张英。不要仅因收到/好/安排、"
        "单独媒体、转发或明显代问就指定；多人均主动且无法分清时owner=null并说明歧义。"
        "owner_basis={kind:assigned或inferred或unassigned,reason:简短判断理由,locators:[本事项证据定位]}。"
        "inferred的原sender须通过OFFICE.aliases精确对应所选办公室姓名，assigned须有包含该办公室姓名或已确认昵称的明确交办原文；"
        "不输出manual类型，不虚构置信百分数。无依据时owner=null、kind=unassigned、locators=[]；存在多人歧义时可引用真实歧义消息。")


def source_evidence(item):
    return [{**e, "locator": e.get("locator") or str(e.get("source_id", "")) + "|" + str(e.get("source_ref", ""))}
            for e in item.get("evidence", [])]


def verify_owner(item, office=None):
    """消息已由调用者回填真实元数据。自由判断的语义仍需人工复核。"""
    basis = item.get("owner_basis")
    if basis is None:  # 兼容旧快照，不制造旧负责人依据。
        return
    owner = item.get("owner")
    if office is not None and owner:
        from app.office import canonical_name, constrain_owner
        canonical = canonical_name(owner, office)
        if not canonical:
            constrain_owner(item, office)
            return
        owner = item["owner"] = canonical
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
        def sender_matches(sender):
            return canonical_name(sender, office) == owner if office is not None else sender == owner
        if basis["kind"] == "inferred" and not any(sender_matches(e["sender"]) for e in meaningful):
            raise ValueError("推定跟进人必须是所引消息真实发言人")
        aliases = [a for a, n in office["aliases"].items() if n == owner] if office is not None else [owner]
        if basis["kind"] == "assigned" and not any(any(a in e["text"] for a in aliases) for e in meaningful):
            raise ValueError("明确分派依据需包含被分派人姓名")
    item["owner_basis"] = {"kind": basis["kind"], "reason": reason.strip(), "locators": list(dict.fromkeys(locators)),
                           "evidence": evidence, "checked_at": now()}


def decorate_owner(item, overrides=None, office=None):
    overrides = overrides or {}
    basis = item.get("owner_basis")
    if "owner" in overrides and "owner_basis" not in overrides:
        basis = {"kind": "manual_legacy", "reason": "沿用既有人工保存的负责人；旧记录未单独区分修改与同值保存，可重新确认。", "evidence": []}
    if not isinstance(basis, dict):
        basis = {"kind": "legacy" if item.get("owner") else "unassigned", "reason": "旧记录未单独保存负责人判断依据。", "evidence": []}
    item["owner_basis"] = {**basis, "label": LABELS.get(basis.get("kind"), "依据待核"),
                           "confirmed": basis.get("kind") == "manual"}
    if office is not None:
        from app.office import canonical_name, constrain_owner
        if item.get("owner") and not canonical_name(item["owner"], office) and "owner" in overrides:
            item["previous_manual_owner"] = item["owner"]
            constrain_owner(item, office)
            item["owner_basis"] = {**item["owner_basis"], "label": "人工记录保留·办公室待分派", "confirmed": False, "office_scope_valid": False}
        else:
            constrain_owner(item, office)
            item["owner_basis"]["label"] = LABELS.get(item["owner_basis"].get("kind"), "依据待核")
        item["communication_participants"] = list(dict.fromkeys(e.get("sender") for e in source_evidence(item) if e.get("sender")))
    return item


def preserve_assignment(previous, incoming, office=None):
    """新的主动发言推定不能取代已有明示分派。"""
    from app.office import canonical_name
    eligible = office is None or canonical_name(previous.get("owner"), office)
    if eligible and (previous.get("owner_basis") or {}).get("kind") == "assigned" and (incoming.get("owner_basis") or {}).get("kind") in {"inferred", "unassigned"}:
        incoming["owner"] = previous.get("owner")
        incoming["owner_basis"] = previous["owner_basis"]


def manual_owner(base, overrides, changes, actor, office=None):
    """仅改变姓名或专门确认才产生人工依据；同值保存不冻结AI候选。"""
    confirmation = changes.pop("owner_confirmation", None)
    if confirmation not in {None, "confirmed"}:
        raise ValueError("负责人确认请求无效")
    current = overrides.get("owner", base.get("owner")) or None
    value = changes.get("owner", current)
    value = (value or "").strip() or None
    if office is not None:
        from app.office import canonical_name
        if value:
            canonical = canonical_name(value, office)
            if not canonical and (confirmation or value != current):
                raise ValueError("跟进负责人只能选择办公室名单中的人员")
            value = canonical or value
        current = canonical_name(current, office) or current
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
