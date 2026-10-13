"""售后独立台账：保留原状态、月份、来源及人工处理结果。"""
import hashlib
import json
from contextlib import nullcontext
from app.store import now
from app.workdays import priority

STATUSES = {"待检查", "已给建议", "资料已发", "当次恢复", "完成验收", "结果待核对", "待并案"}
COMPANIES = {"大洋": "dayang", "云艺": "yunyi", "金耐": "jinnai"}


class AfterSales:
    def __init__(self, store):
        self.store = store
        with store.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS after_sales(id TEXT PRIMARY KEY,base_json TEXT NOT NULL,override_json TEXT NOT NULL DEFAULT '{}')")

    def import_ledger(self, data, source_hash, connection=None):
        import re
        if not isinstance(data, dict) or not re.fullmatch(r"\d{4}-\d{2}", data.get("period", "")) or not isinstance(data.get("cases"), list):
            raise ValueError("售后台账需有月份与cases列表")
        period = data["period"]
        from datetime import datetime
        datetime.strptime(period, "%Y-%m")
        if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise ValueError("售后来源SHA256无效")
        prepared, seen = [], set()
        for case in data["cases"]:
            if not isinstance(case, dict) or case.get("company") not in COMPANIES or case.get("status") not in STATUSES or not isinstance(case.get("include_in_total"), bool):
                raise ValueError("售后公司、状态或统计标记无效")
            if not isinstance(case.get("id"), str) or not case["id"] or len(case["id"]) > 120:
                raise ValueError("售后事项ID无效")
            if case["status"] == "待并案" and case["include_in_total"]:
                raise ValueError("待并案事项不能计入主台账")
            key = period + ":" + case["company"] + ":" + case["id"]
            if key in seen:
                raise ValueError("售后事项ID重复")
            seen.add(key)
            evidence = case.get("evidence")
            if not isinstance(evidence, list) or not evidence or any(not isinstance(e, dict) or e.get("source_id") != COMPANIES[case["company"]] or not isinstance(e.get("source_ref"), str) or not e["source_ref"] for e in evidence):
                raise ValueError("售后事项缺少本公司来源定位")
            if case["status"] == "完成验收" and not case.get("acceptance_note"):
                raise ValueError("完成验收需填写明确验收依据")
            prepared.append({**case, "id": key, "source_case_id": case["id"], "period": period,
                             "source_sha256": source_hash, "tracking_started_at": now(),
                             "tracking_note": "首次接入起跟踪；历史月份记录不代表当前设备状态，需复核"})
        with (nullcontext(connection) if connection is not None else self.store.connect()) as conn:
            for row in prepared:
                historical_id = row.get("historical_case_id")
                if historical_id:
                    candidates = [json.loads(r[0]) for r in conn.execute("SELECT base_json FROM after_sales")]
                    matches = [r for r in candidates if r["company"] == row["company"] and historical_id in {r["id"], r["source_case_id"]}]
                    if len(matches) != 1:
                        raise ValueError("历史售后关联无法唯一匹配，请保留待并案")
                    row["id"] = matches[0]["id"]
                    row["source_case_id"] = matches[0]["source_case_id"]
                    row["last_evidence_period"] = row["period"]
                    row["period"] = matches[0]["period"]
                old = conn.execute("SELECT base_json FROM after_sales WHERE id=?", (row["id"],)).fetchone()
                if old:
                    previous = json.loads(old[0])
                    incoming_owner = row.get("owner")
                    has_owner_basis = "owner_basis" in row
                    row = {**previous, **{k: v for k, v in row.items() if v is not None}}
                    if has_owner_basis:
                        row["owner"] = incoming_owner
                    from app.ownership import preserve_assignment
                    preserve_assignment(previous, row)
                    if previous.get("status") == "完成验收":
                        row["status"] = "完成验收"
                        row["acceptance_note"] = previous.get("acceptance_note")
                    row["tracking_started_at"] = previous["tracking_started_at"]
                    if previous.get("progress_at") and previous["progress_at"] > (row.get("progress_at") or ""):
                        row["progress_at"] = previous["progress_at"]
                    refs = {(e["source_id"], e["source_ref"]): e for e in previous["evidence"] + row["evidence"]}
                    row["evidence"] = list(refs.values())
                    row["source_hashes"] = list(dict.fromkeys(previous.get("source_hashes", [previous["source_sha256"]]) + [source_hash]))
                    conn.execute("UPDATE after_sales SET base_json=? WHERE id=?", (json.dumps(row, ensure_ascii=False), row["id"]))
                else:
                    conn.execute("INSERT INTO after_sales(id,base_json) VALUES(?,?)", (row["id"], json.dumps(row, ensure_ascii=False)))
        return {"imported": len(prepared), "period": period}

    def snapshot(self):
        from app.ownership import decorate_owner
        with self.store.connect() as conn:
            items = []
            for row in conn.execute("SELECT * FROM after_sales"):
                item = json.loads(row["base_json"])
                source_progress = item.get("progress_at")
                overrides = json.loads(row["override_json"])
                item.update(overrides)
                decorate_owner(item, overrides)
                if source_progress and source_progress > (item.get("progress_at") or ""):
                    item["progress_at"] = source_progress
                item["priority"] = priority(item)
                items.append(item)
        items.sort(key=lambda i: (i["status"] == "完成验收", not i["priority"]["urgent"], i["id"]))
        return {"cases": items, "counted": sum(i["include_in_total"] for i in items),
                "periods": sorted({i["period"] for i in items}),
                "note": "已给建议、资料已发、当次恢复不等于完成验收；历史资料需核对当前结果"}

    def update(self, item_id, changes, actor):
        if actor.get("role") not in {"admin", "editor"}:
            raise ValueError("该账号仅有查看权限")
        if not changes or set(changes) - {"status", "owner", "owner_confirmation", "progress_note"} or any(not isinstance(v, str) or len(v) > (80 if k == "owner" else 2000) for k, v in changes.items()):
            raise ValueError("仅可修改售后状态、负责人及处理说明")
        if changes.get("status") and changes["status"] not in STATUSES:
            raise ValueError("售后状态无效")
        note = changes.get("progress_note", "").strip()
        if note in {"好", "好的", "收到", "OK", "ok", "安排", "已阅", "查看"}:
            raise ValueError("请填写实际处理内容，收悉或安排不能重新计时")
        if changes.get("status") == "完成验收" and not note:
            raise ValueError("完成验收需填写实际结果和验收依据")
        with self.store.connect() as conn:
            row = conn.execute("SELECT base_json,override_json FROM after_sales WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError("售后事项不存在")
            overrides = json.loads(row["override_json"])
            from app.ownership import manual_owner
            manual_owner(json.loads(row["base_json"]), overrides, changes, actor)
            overrides.update({k: v for k, v in changes.items() if k != "progress_note"})
            if note:
                timestamp = now()
                overrides["progress_at"] = timestamp
                overrides.setdefault("manual_updates", []).append({"time": timestamp, "username": actor["username"], "name": actor["display_name"], "note": note, "status": changes.get("status")})
            conn.execute("UPDATE after_sales SET override_json=? WHERE id=?", (json.dumps(overrides, ensure_ascii=False), item_id))
            conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)", (now(), actor["username"], "update_after_sales", item_id, json.dumps(changes, ensure_ascii=False)))
        return next(i for i in self.snapshot()["cases"] if i["id"] == item_id)
