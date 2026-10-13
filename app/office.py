"""办公室跟进名单与精确昵称映射；名单外发言人不能自动成为跟进人。"""
import json
from pathlib import Path

DEFAULT_NAMES = ["李睿琪", "张英", "银小芹", "王星", "郭逸", "张利伟"]
DEFAULT_ALIASES = {"liruiqi": "李睿琪", "zhangying": "张英", "yinxiaoqin": "银小芹", "wangxing": "王星",
                   "guoyi": "郭逸", "zhangliwei": "张利伟", "李睿琪（本人）": "李睿琪",
                   "大洋艺术张英": "张英", "设计郭逸": "郭逸", "设计张利伟": "张利伟"}


def office_policy(workspace):
    path = Path(workspace) / "private" / "office_staff.json"
    value = json.loads(path.read_text("utf-8-sig")) if path.is_file() else {"names": DEFAULT_NAMES, "aliases": DEFAULT_ALIASES}
    names, aliases = value.get("names"), value.get("aliases", {})
    if (not isinstance(names, list) or not names or len(names) > 100 or any(not isinstance(n, str) or not n.strip() or len(n) > 80 for n in names)
            or len(set(names)) != len(names) or not isinstance(aliases, dict)
            or any(not isinstance(a, str) or not a.strip() or len(a) > 100 or n not in names for a, n in aliases.items())
            or any(a in names and a != n for a, n in aliases.items())):
        raise ValueError("办公室名单或昵称映射无效，请核对本机office_staff.json")
    return {"names": list(names), "aliases": {**{n: n for n in names}, **aliases}}


def canonical_name(value, policy):
    return policy["aliases"].get(value.strip()) if isinstance(value, str) else None


def constrain_owner(item, policy):
    """兼容导入与旧数据：办公室以外的自动负责人撤回为待分派。"""
    original = item.get("owner")
    if not original:
        return item
    canonical = canonical_name(original, policy)
    if canonical:
        item["owner"] = canonical
        return item
    basis = item.get("owner_basis") if isinstance(item.get("owner_basis"), dict) else {}
    item["owner"] = None
    item["owner_basis"] = {"kind": "unassigned", "reason": "原负责人候选“" + str(original) + "”不在已确认办公室名单内，办公室跟进人待分派。",
                           "evidence": basis.get("evidence", []), "locators": basis.get("locators", []),
                           "rejected_owner": original, "checked_at": basis.get("checked_at")}
    return item
