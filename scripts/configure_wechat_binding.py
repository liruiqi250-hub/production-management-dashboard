"""仅在接收者电脑保存明确授权的微信目录绑定，不连接微信、不读取消息。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"db_dir", "account", "self_username"}


class BindingError(ValueError):
    """错误文字不得包含用户提供的账号或目录。"""


def reject_links(path: Path) -> None:
    """解析前检查每级已存在路径，阻止符号链接及 Windows junction/reparse。"""
    current = Path(os.path.abspath(path))
    for part in (current, *current.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise BindingError("目录包含链接或重解析点，请使用真实本机目录。")


def validated_binding(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise BindingError("绑定资料必须且只能包含 db_dir、account、self_username 三个字段。")
    if any(not isinstance(item, str) or not item or item != item.strip() or
           any(ord(char) < 32 for char in item) for item in value.values()):
        raise BindingError("绑定字段必须是非空文本，不能含控制字符或首尾空格。")
    for field in ("account", "self_username"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value[field]):
            raise BindingError("账号目录名及微信身份标识只允许字母、数字、下划线和短横线。")
    raw_dir = value["db_dir"]
    if raw_dir.startswith(("\\\\", "//")) or raw_dir.startswith(("\\?", "\\.")):
        raise BindingError("微信数据目录必须位于本机磁盘，不能使用网络共享或设备路径。")
    folder = Path(raw_dir)
    if not folder.is_absolute() or ".." in folder.parts:
        raise BindingError("微信数据目录必须是本机磁盘的完整路径，不能含上级目录跳转。")
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(folder.anchor)) not in (2, 3):
            raise BindingError("微信数据目录必须位于本机可写磁盘，不能使用网络映射盘。")
    reject_links(folder)
    root = folder.resolve(strict=True)
    if not root.is_dir():
        raise BindingError("微信数据根目录不存在或不是目录。")
    selected = root / value["account"]
    reject_links(selected / "db_storage")
    if not selected.is_dir() or selected.resolve().parent != root or not (selected / "db_storage").is_dir():
        raise BindingError("所选账号目录下未找到 db_storage，请确认数据根目录与账号目录名。")
    return {"db_dir": str(root), "account": value["account"], "self_username": value["self_username"]}


def binding_file(root: Path = ROOT) -> Path:
    reject_links(root)
    root = root.resolve(strict=True)
    path = root / "private" / "wechat_binding.json"
    reject_links(path)
    if path.resolve(strict=False).parent != root / "private":
        raise BindingError("绑定输出位置不属于本项目 private 目录。")
    return path


def check_binding(root: Path = ROOT) -> dict:
    try:
        path = binding_file(root)
        if not path.is_file():
            return {"status": "needs_configuration", "code": "binding_missing",
                    "detail": "未配置本机微信绑定；由接收者确认账号后配置。", "wechat_connected": False}
        if path.stat().st_size > 8192:
            raise BindingError("绑定资料大小异常，请检查本机配置文件。")
        validated_binding(json.loads(path.read_text(encoding="utf-8-sig")))
        return {"status": "ready", "code": "binding_structure_valid",
                "detail": "绑定字段及本机目录结构有效；微信登录、身份授权和真实采集仍需单独验收。",
                "wechat_connected": False}
    except (BindingError, OSError, ValueError, TypeError):
        return {"status": "error", "code": "binding_invalid",
                "detail": "本机绑定字段、目录结构或文件权限不符合要求；没有连接微信。", "wechat_connected": False}


def save_binding(value: object, *, root: Path = ROOT, replace: bool = False) -> dict:
    checked = validated_binding(value)
    path = binding_file(root)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    reject_links(path)
    if path.exists() and not replace:
        raise BindingError("已存在本机微信绑定，未覆盖；确认更换授权账号后才可显式使用 --replace。")
    data = (json.dumps(checked, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".wechat_binding_", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        reject_links(path)
        if replace:
            os.replace(temporary_path, path)
        else:
            # 创建硬链接为原子“不覆盖”发布，避免 exists() 与写入之间的竞争。
            try:
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise BindingError("已有绑定在配置时发生变更，未覆盖；请重新检查。") from exc
        return {"status": "ready", "code": "binding_saved",
                "detail": "本机微信绑定已保存；没有连接微信，也未安装定时任务。", "wechat_connected": False}
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-dir", help="微信数据根目录：所选账号目录的上一层，不含账号目录名")
    parser.add_argument("--account", help="已确认授权的本机账号目录名")
    parser.add_argument("--self-username", help="已确认授权账号的微信身份标识，不是显示昵称")
    parser.add_argument("--replace", action="store_true", help="明确更换账号时覆盖已有本机绑定")
    parser.add_argument("--check", action="store_true", help="仅检查绑定字段和本机目录结构，不连接微信")
    parser.add_argument("--json", action="store_true", help="输出不含账号及目录值的 JSON")
    args = parser.parse_args(argv)
    try:
        if args.check:
            if args.replace or any((args.db_dir, args.account, args.self_username)):
                raise BindingError("--check 不能与写入参数同时使用。")
            result = check_binding()
        else:
            if not all((args.db_dir, args.account, args.self_username)):
                raise BindingError("请同时提供 --db-dir、--account、--self-username；不能猜测授权账号。")
            if os.name != "nt":
                raise BindingError("此绑定入口仅支持 Windows 本机微信数据。")
            result = save_binding({"db_dir": args.db_dir, "account": args.account, "self_username": args.self_username}, replace=args.replace)
    except (BindingError, OSError, ValueError, TypeError) as exc:
        message = str(exc) if isinstance(exc, BindingError) else "配置失败；请检查本机目录与写入权限。"
        result = {"status": "error", "code": "binding_configuration_failed", "detail": message, "wechat_connected": False}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["detail"])
    return {"ready": 0, "needs_configuration": 2, "error": 1}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
