"""本机私有沟通案例：辅助理解语境，不改变证据范围或关闭权限。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

FILENAME = "communication_profiles_V1.0.0.json"
MAX_BYTES = 128 * 1024
SOURCES = {f"production_{i:02}" for i in range(1, 15)}


def _text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _period(value):
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        return False
    try:
        start, end = (datetime.fromisoformat(value[k]) for k in ("start", "end"))
        return start.tzinfo is not None and end.tzinfo is not None and start < end
    except (ValueError, TypeError):
        return False


def load_communication_profile(workspace, office):
    """固定本机路径；无配置返回None，非法配置拒绝进入自动分析。"""
    path = Path(workspace) / "private" / FILENAME
    if not path.exists():
        return None, {"loaded": False}
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            raise ValueError
        value = json.loads(data.decode("utf-8-sig"))
        fields = {"schema", "version", "updated_at", "requested_period", "observed_period", "coverage_note", "speakers", "rules", "examples"}
        if (not isinstance(value, dict) or set(value) != fields or value["schema"] != 1
                or not _text(value["version"], 30) or not _text(value["coverage_note"], 2000)
                or not _period(value["requested_period"]) or not _period(value["observed_period"])):
            raise ValueError
        updated = datetime.fromisoformat(value["updated_at"])
        if updated.tzinfo is None:
            raise ValueError
        requested, observed = value["requested_period"], value["observed_period"]
        if not (datetime.fromisoformat(requested["start"]) <= datetime.fromisoformat(observed["start"])
                < datetime.fromisoformat(observed["end"]) <= datetime.fromisoformat(requested["end"])):
            raise ValueError
        speakers = value["speakers"]
        if not isinstance(speakers, list) or not 1 <= len(speakers) <= 20:
            raise ValueError
        names = set()
        for speaker in speakers:
            if (not isinstance(speaker, dict) or set(speaker) != {"name", "aliases", "identity_sha256", "notes"}
                    or speaker["name"] not in office["names"] or speaker["name"] in names
                    or not isinstance(speaker["aliases"], list) or len(speaker["aliases"]) > 20
                    or any(office["aliases"].get(a) != speaker["name"] for a in speaker["aliases"])
                    or not re.fullmatch(r"[0-9a-f]{64}", speaker["identity_sha256"])
                    or not _text(speaker["notes"], 3000)):
                raise ValueError
            names.add(speaker["name"])
        rules = value["rules"]
        if not isinstance(rules, list) or not 1 <= len(rules) <= 40 or any(not _text(r, 2000) for r in rules):
            raise ValueError
        examples = value["examples"]
        if not isinstance(examples, list) or not 1 <= len(examples) <= 80:
            raise ValueError
        for example in examples:
            if (not isinstance(example, dict) or set(example) != {"speaker", "source_id", "refs", "context", "meaning", "boundary"}
                    or example["speaker"] not in names or example["source_id"] not in SOURCES
                    or not isinstance(example["refs"], list) or not 1 <= len(example["refs"]) <= 20
                    or any(not isinstance(ref, str) or not re.fullmatch(re.escape(example["source_id"]) + r"\|message[\\/]message_\d+\.db:\d+", ref) for ref in example["refs"])
                    or any(not _text(example[k], 3000) for k in ("context", "meaning", "boundary"))):
                raise ValueError
    except (ValueError, TypeError, KeyError, OSError, UnicodeError):
        raise ValueError("本机沟通理解资料无效，请核对private中的沟通资料；正式台账保留") from None
    receipt = {"loaded": True, "version": value["version"], "sha256": hashlib.sha256(data).hexdigest(),
               "requested_period": requested, "observed_period": observed,
               "speaker_count": len(speakers), "rule_count": len(rules), "example_count": len(examples)}
    return value, receipt


def explicit_short_reply_context(match, evidence):
    """简短同意只接明确阶段提问；收悉、未来安排及仅媒体不构成放行。"""
    try:
        replied_at = datetime.fromisoformat(match["time"])
        for other in evidence:
            if other is match or other.get("sender") == match.get("sender"):
                continue
            if other["locator"].split("|")[0] != match["locator"].split("|")[0]:
                continue
            delta = (replied_at - datetime.fromisoformat(other["time"])).total_seconds()
            if not 0 <= delta <= 3600:
                continue
            text = other["text"].split("↳ 引用", 1)[0].strip()
            stage = r"造型|纹理|纹路|钢架|方案|参考|光面|粘袜子|包海绵|下一步|做起走|整起走|电瓶|天能|插座|模具|颜色|配色|色样|尺寸|预计日期|参数"
            question = r"可以.{0,30}[吗不？?]|可不可以|能不能|可否|是否|是不是|[？?]"
            if len(text) <= 300 and re.search(stage, text) and re.search(question, text):
                return True
    except (ValueError, KeyError, TypeError):
        return False
    return False
