"""清单的简短展示文案；不改变原始说明或证据。"""
from __future__ import annotations

import hashlib
import json
import re


def summary_fingerprint(item):
    fields = ("title", "summary", "next_step", "status", "closure_scope", "last_seen", "source_last_seen", "manual_updated_at")
    value = {key: item.get(key) for key in fields}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def compact_text(value, limit=80):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip("，；、 ") + "…"


def list_summary(item, saved=None):
    if saved and saved.get("fingerprint") == summary_fingerprint(item):
        return compact_text(saved.get("text"))
    if item.get("status") == "已解决":
        scope = item.get("closure_scope") or "以详情中的完成说明为准"
        return compact_text("已确认范围：" + scope, 70)
    step = re.sub(r"\s+", " ", str(item.get("next_step") or "")).strip()
    if step:
        first = re.split(r"[。；;]", step, maxsplit=1)[0]
        return compact_text(first, 65)
    return "待补充处理进展，点击查看完整说明。"
