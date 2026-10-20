"""安装生产资料所需的锁定 Windows CPython 3.13 官方轮子，不使用 pip 或系统包。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import urllib.parse
import urllib.request
import uuid
import zipfile

try:
    from scripts.publish_github import PublishError, _safe_path, _relative_name
except ModuleNotFoundError:
    from publish_github import PublishError, _safe_path, _relative_name


COMPONENTS = {
    "openpyxl": ("3.1.5", "openpyxl-3.1.5-py2.py3-none-any.whl", ("openpyxl",)),
    "et-xmlfile": ("2.0.0", "et_xmlfile-2.0.0-py3-none-any.whl", ("et_xmlfile",)),
    "pypdf": ("6.21.1", "pypdf-6.21.1-py3-none-any.whl", ("pypdf",)),
    "pypdfium2": ("5.14.0", "pypdfium2-5.14.0-py3-none-win_amd64.whl",
                  ("pypdfium2", "pypdfium2_raw", "pypdfium2_cfg", "pypdfium2_cli")),
    "pillow": ("13.0.0", "pillow-13.0.0-cp313-cp313-win_amd64.whl", ("PIL",)),
}
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_BYTES = 250 * 1024 * 1024
SHA = re.compile(r"[a-f0-9]{64}")


def _official_url(value):
    if not isinstance(value, str):
        return False
    url = urllib.parse.urlsplit(value)
    return (url.scheme == "https" and url.hostname == "files.pythonhosted.org"
            and not url.username and not url.password and url.port in (None, 443)
            and url.path.startswith("/packages/") and not url.query and not url.fragment)


class OfficialRedirectOnly(urllib.request.HTTPRedirectHandler):
    """在重定向请求发出前核验，不能先访问任意主机再检查最终地址。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _official_url(newurl):
            raise ValueError("生产资料依赖禁止重定向到非官方来源")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_lock(lock):
    if (not isinstance(lock, dict) or lock.get("schema") != 1
            or lock.get("platform") != "windows-x64" or lock.get("python") != "cp313"
            or not isinstance(lock.get("components"), list) or len(lock["components"]) != len(COMPONENTS)):
        raise ValueError("生产资料组件锁文件版本或平台无效")
    seen = set()
    for item in lock["components"]:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or item["id"] not in COMPONENTS or item["id"] in seen):
            raise ValueError("生产资料组件未知或重复")
        seen.add(item["id"])
        version, filename, _ = COMPONENTS[item["id"]]
        if item.get("version") != version or item.get("archive_filename") != filename:
            raise ValueError("生产资料组件版本或 Windows CPython 3.13 轮子不匹配")
        if (not _official_url(item.get("url"))
                or urllib.parse.unquote(urllib.parse.urlsplit(item["url"]).path.rsplit("/", 1)[-1]) != filename):
            raise ValueError("生产资料组件必须来自匹配轮子的官方地址")
        expected_metadata = f"https://pypi.org/pypi/{'Pillow' if item['id'] == 'pillow' else item['id']}/{version}/json"
        if item.get("metadata_url") != expected_metadata:
            raise ValueError("生产资料组件元数据来源无效")
        if type(item.get("archive_bytes")) is not int or not 0 < item["archive_bytes"] <= MAX_ARCHIVE_BYTES:
            raise ValueError("生产资料组件锁定大小无效")
        for key in ("archive_sha256", "metadata_sha256", "license_sha256"):
            if not isinstance(item.get(key), str) or not SHA.fullmatch(item[key]):
                raise ValueError("生产资料组件摘要无效")
        licenses = item.get("licenses")
        if not isinstance(licenses, list) or not licenses:
            raise ValueError("生产资料组件缺少许可证记录")
        distribution = filename.split("-", 1)[0]
        prefix = f"{distribution}-{version}.dist-info/"
        license_paths = set()
        for license_info in licenses:
            if not isinstance(license_info, dict) or not isinstance(license_info.get("path"), str):
                raise ValueError("生产资料组件许可证记录无效")
            path = _relative_name(license_info.get("path", ""))
            if (not path.startswith(prefix) or path in license_paths
                    or not isinstance(license_info.get("sha256"), str) or not SHA.fullmatch(license_info["sha256"])
                    or license_info.get("source_url") != item["url"] + "#" + path):
                raise ValueError("生产资料组件许可证来源或路径无效")
            license_paths.add(path)
        if not any(info["path"] == item.get("license_path") and info["sha256"] == item["license_sha256"] for info in licenses):
            raise ValueError("生产资料主许可证记录不一致")
    return lock


def obtain_archive(item, cache, *, offline=False):
    path = _safe_path(Path(cache), item["archive_filename"])
    if path.exists():
        if not path.is_file() or path.stat().st_size != item["archive_bytes"] or _digest(path) != item["archive_sha256"]:
            raise ValueError("生产资料依赖缓存校验失败：" + path.name)
        return path
    if offline:
        raise ValueError("缺少生产资料依赖离线缓存：" + path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = _safe_path(path.parent, path.name + ".part")
    if partial.exists():
        raise ValueError("生产资料依赖存在未完成的下载")
    try:
        opener = urllib.request.build_opener(OfficialRedirectOnly())
        request = urllib.request.Request(item["url"], headers={"User-Agent": "ProductionDocuments-Runtime/1"})
        with opener.open(request, timeout=45) as response:
            if not _official_url(response.geturl()):
                raise ValueError("生产资料依赖下载来源无效")
            length, digest = 0, hashlib.sha256()
            with partial.open("xb") as target:
                while block := response.read(1024 * 1024):
                    length += len(block)
                    if length > item["archive_bytes"]:
                        raise ValueError("生产资料依赖下载超过锁定大小")
                    digest.update(block)
                    target.write(block)
        if length != item["archive_bytes"] or digest.hexdigest() != item["archive_sha256"]:
            raise ValueError("生产资料依赖下载校验失败")
        partial.replace(path)
    except Exception:
        if partial.is_file():
            partial.unlink()
        raise
    return path


def _plan_archive(archive, item, target, seen):
    version, filename, modules = COMPONENTS[item["id"]]
    distribution = filename.split("-", 1)[0]
    allowed_roots = {*modules, f"{distribution}-{version}.dist-info"}
    planned, total = [], 0
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            raw = entry.filename[:-1] if entry.is_dir() else entry.filename
            name = _relative_name(raw)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError("生产资料轮子包含链接或特殊文件")
            if name.split("/", 1)[0] not in allowed_roots or name.casefold().endswith((".pth", "._pth")):
                raise ValueError("生产资料轮子包含未授权路径或启动文件")
            if entry.is_dir():
                continue
            if name.casefold() in seen:
                raise ValueError("生产资料轮子包含重复路径")
            seen.add(name.casefold())
            total += entry.file_size
            if total > MAX_EXTRACTED_BYTES or entry.flag_bits & 1:
                raise ValueError("生产资料轮子解压大小或加密状态无效")
            planned.append((name, _safe_path(target, name)))
        names = {name for name, _ in planned}
        if any(module + "/__init__.py" not in names for module in modules):
            raise ValueError("生产资料轮子缺少必要模块")
        for info in item["licenses"]:
            if info["path"] not in names or hashlib.sha256(package.read(info["path"])).hexdigest() != info["sha256"]:
                raise ValueError("生产资料轮子许可证校验失败")
    return planned, total


def build_production_document_runtime(root, cache, source, *, offline=False):
    """校验整组依赖后安装到隔离解释器，允许重复执行，不运行轮子内代码。"""
    try:
        root, cache, source = Path(root), Path(cache), Path(source)
        lock_path = _safe_path(source, "dependencies/production-documents.lock.json")
        lock = validate_lock(json.loads(lock_path.read_text("utf-8-sig")))
        target = _safe_path(root, "vendor/python/site-packages")
        pth = _safe_path(root, "vendor/python/python313._pth")
        lines = pth.read_text("utf-8-sig").splitlines()
        if any(line.strip().startswith("import ") for line in lines):
            raise ValueError("生产资料隔离解释器不得启用启动导入")
        archives = [(item, obtain_archive(item, cache, offline=offline)) for item in lock["components"]]
        plans, seen, total = [], set(), 0
        for item, archive in archives:
            planned, size = _plan_archive(archive, item, target, seen)
            total += size
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("生产资料整组依赖解压大小超过上限")
            plans.append((archive, planned))
        # 全量路径、摘要、许可证和容量预检完成后，才开始写目标目录。
        for archive, planned in plans:
            with zipfile.ZipFile(archive) as package:
                for name, path in planned:
                    data = package.read(name)
                    if path.is_file() and path.read_bytes() == data:
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = _safe_path(path.parent, path.name + "." + uuid.uuid4().hex + ".tmp")
                    try:
                        temporary.write_bytes(data)
                        temporary.replace(path)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
        if "site-packages" not in lines:
            lines.append("site-packages")
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        manifest = {"schema": 1, "platform": "windows-x64", "python": "cp313",
                    "lock_sha256": _digest(lock_path), "files": len(seen),
                    "components": [{key: item[key] for key in ("id", "version", "archive_sha256", "url", "licenses")}
                                   for item in lock["components"]]}
        _safe_path(root, "vendor/python/PRODUCTION_DOCUMENT_RUNTIME.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        return manifest
    except PublishError as exc:
        raise ValueError("生产资料运行组件路径校验失败") from exc
