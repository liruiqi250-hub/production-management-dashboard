"""流式核验完整便携 ZIP 的清单、文件哈希和业务资料边界，不解压或执行。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

try:
    from scripts.publish_github import PublishError, _relative_name
except ModuleNotFoundError:
    from publish_github import PublishError, _relative_name

FORBIDDEN_ROOTS = {".git", "private", "acquisition", "analysis", "runtime", "outputs"}
PRIVATE_NAMES = {"wechat_binding.json", "wechat_binding_status.json", "production.sqlite3"}


class AuditError(RuntimeError):
    pass


def audit_archive(path: Path, *, expected_version: str | None = None) -> dict:
    entries, seen, roots = {}, set(), set()
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            name = _relative_name(entry.filename.rstrip("/"))
            parts = PurePosixPath(name).parts
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise AuditError("完整包含链接或特殊文件")
            if name.casefold() in seen:
                raise AuditError("完整包含大小写冲突或重复文件")
            seen.add(name.casefold())
            roots.add(parts[0])
            if entry.is_dir():
                continue
            if len(parts) < 2:
                raise AuditError("完整包必须有一个顶层目录")
            relative = "/".join(parts[1:])
            if (parts[1].casefold() in FORBIDDEN_ROOTS
                    or any(part.casefold().startswith(".env") for part in parts[1:])
                    or PurePosixPath(relative).name.casefold() in PRIVATE_NAMES
                    or any(term in PurePosixPath(relative).name for term in ("初始账号", "账号密码", "聊天记录"))):
                raise AuditError("完整包含禁止发布的本机资料路径：" + relative)
            entries[relative] = entry
        if len(roots) != 1 or "SHA256SUMS.json" not in entries:
            raise AuditError("完整包顶层目录或校验清单缺失")
        manifest_entry = entries["SHA256SUMS.json"]
        if manifest_entry.file_size > 32 * 1024 * 1024:
            raise AuditError("完整包校验清单过大")
        manifest = json.loads(archive.read(manifest_entry).decode("utf-8-sig"))
        version = manifest.get("version", "")
        if (manifest.get("schema") != 1 or manifest.get("platform") != "windows-x64"
                or not re.fullmatch(r"\d+\.\d+\.\d+", version)
                or (expected_version and version != expected_version)):
            raise AuditError("完整包版本或平台不符合要求")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(entries) - {"SHA256SUMS.json"}:
            raise AuditError("完整包包含未列入清单的文件或缺少列出的文件")
        total = 0
        for name, spec in files.items():
            _relative_name(name)
            entry = entries[name]
            if (not isinstance(spec, dict) or spec.get("bytes") != entry.file_size
                    or not re.fullmatch(r"[0-9a-f]{64}", spec.get("sha256", ""))):
                raise AuditError("完整包文件大小或哈希声明无效：" + name)
            digest = hashlib.sha256()
            with archive.open(entry) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != spec["sha256"]:
                raise AuditError("完整包文件 SHA-256 不匹配：" + name)
            total += entry.file_size
        if "VERSION" not in entries or archive.read(entries["VERSION"]).decode("utf-8-sig").strip() != version:
            raise AuditError("完整包 VERSION 与校验清单不一致")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"version": version, "files": len(entries), "archive_bytes": path.stat().st_size,
            "extracted_bytes": total, "sha256": digest, "all_file_hashes_verified": True,
            "business_paths_absent": True, "executed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--version")
    args = parser.parse_args()
    try:
        result = audit_archive(args.archive, expected_version=args.version)
    except (AuditError, PublishError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
