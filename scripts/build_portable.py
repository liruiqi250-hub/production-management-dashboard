"""按锁文件构建 Windows x64 便携 ZIP；仅打包已跟踪白名单代码及官方依赖。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

try:
    from scripts.publish_github import PublishError, _relative_name, _safe_path, _tracked, collect_files
except ModuleNotFoundError:
    from publish_github import PublishError, _relative_name, _safe_path, _tracked, collect_files


class BundleError(RuntimeError):
    """只报告文件名和操作，不输出本机凭据或文件内容。"""


MAX_ARCHIVE_BYTES = 600 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1500 * 1024 * 1024
COMPONENT_LAYOUT = {
    "python": ("vendor/python", "", "LICENSE.txt", "python.exe"),
    "chromium": ("vendor/chromium", "chrome-headless-shell-win64/", "LICENSE.headless_shell",
                 "chrome-headless-shell.exe"),
}


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_lock(data: bytes) -> dict:
    try:
        lock = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BundleError("依赖锁文件不是有效的 UTF-8 JSON") from exc
    if (not isinstance(lock, dict) or lock.get("schema") != 1
            or lock.get("platform") != "windows-x64"
            or not isinstance(lock.get("components"), list)):
        raise BundleError("依赖锁文件版本或平台无效")
    seen = set()
    for component in lock["components"]:
        if not isinstance(component, dict) or component.get("id") not in COMPONENT_LAYOUT:
            raise BundleError("依赖锁文件包含未知组件")
        ident = component["id"]
        if ident in seen:
            raise BundleError("依赖锁文件包含重复组件")
        seen.add(ident)
        target, prefix, license_name, _ = COMPONENT_LAYOUT[ident]
        if component.get("target") != target or component.get("strip_prefix") != prefix:
            raise BundleError(f"依赖目录布局无效：{ident}")
        version = component.get("version", "")
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", version):
            raise BundleError(f"依赖版本无效：{ident}")
        if ident == "python" and not version.startswith("3.13."):
            raise BundleError("便携解释器当前仅支持 CPython 3.13.x")
        expected_url = (f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip"
                        if ident == "python" else
                        f"https://storage.googleapis.com/chrome-for-testing-public/{version}/win64/chrome-headless-shell-win64.zip")
        if component.get("url") != expected_url:
            raise BundleError(f"依赖下载地址必须是匹配版本的官方地址：{ident}")
        filename = component.get("archive_filename", "")
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.zip", filename):
            raise BundleError(f"依赖缓存文件名无效：{ident}")
        license_info = component.get("license")
        if not isinstance(license_info, dict):
            raise BundleError(f"依赖许可证记录无效：{ident}")
        for digest in (component.get("archive_sha256"), license_info.get("sha256")):
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise BundleError(f"依赖 SHA-256 无效：{ident}")
        if license_info.get("path") != license_name:
            raise BundleError(f"依赖许可证路径无效：{ident}")
        size = component.get("archive_bytes")
        if not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE_BYTES:
            raise BundleError(f"依赖压缩包大小无效：{ident}")
    if seen != set(COMPONENT_LAYOUT):
        raise BundleError("依赖锁文件必须同时包含 Python 和无头浏览器")
    return lock


def obtain_archive(component: dict, cache: Path, *, offline: bool = False) -> Path:
    target = _safe_path(cache, component["archive_filename"])
    if target.exists():
        if not target.is_file() or target.stat().st_size != component["archive_bytes"] or sha256_file(target) != component["archive_sha256"]:
            raise BundleError(f"缓存校验失败，拒绝使用：{target.name}")
        return target
    if offline:
        raise BundleError(f"离线缓存中缺少：{target.name}")
    cache.mkdir(parents=True, exist_ok=True)
    partial = _safe_path(cache, target.name + ".part")
    if partial.exists():
        raise BundleError(f"发现未完成下载，请检查后移除：{partial.name}")
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(component["url"], headers={"User-Agent": "ProductionDashboard-Bundle/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            # 原始地址已固定；同时检查最终地址，禁止重定向至非官方主机。
            if urllib.parse.urlsplit(response.geturl()).hostname not in {"www.python.org", "storage.googleapis.com"}:
                raise BundleError(f"依赖重定向至非官方地址：{target.name}")
            with partial.open("xb") as destination:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > component["archive_bytes"]:
                        raise BundleError(f"依赖下载超过锁定大小：{target.name}")
                    digest.update(chunk)
                    destination.write(chunk)
        if size != component["archive_bytes"] or digest.hexdigest() != component["archive_sha256"]:
            raise BundleError(f"依赖下载校验失败：{target.name}")
        partial.replace(target)
    except Exception:
        if partial.is_file():
            partial.unlink()
        raise
    return target


def extract_component(archive: Path, component: dict, root: Path) -> None:
    """完整保留锁定包文件，先预检全部成员，再解压，避免路径穿越和链接。"""
    target_name, prefix, license_name, executable = COMPONENT_LAYOUT[component["id"]]
    destination = _safe_path(root, target_name)
    planned = []
    seen = set()
    total = 0
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            if entry.is_dir():
                continue
            name = _relative_name(entry.filename)
            if prefix and not name.startswith(prefix):
                raise BundleError(f"依赖包含意外的顶层文件：{archive.name}")
            name = name[len(prefix):]
            _relative_name(name)
            if name.casefold() in seen:
                raise BundleError(f"依赖包含重复文件名：{archive.name}")
            seen.add(name.casefold())
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                raise BundleError(f"依赖包含链接或特殊文件：{archive.name}")
            total += entry.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise BundleError(f"依赖解压大小超过上限：{archive.name}")
            planned.append((entry, _safe_path(destination, name)))
        for expected in (license_name.casefold(), executable.casefold()):
            if expected not in seen:
                raise BundleError(f"依赖缺少可执行文件或许可证：{archive.name}")
        license_data = source.read(prefix + license_name)
        if hashlib.sha256(license_data).hexdigest() != component["license"]["sha256"]:
            raise BundleError(f"依赖许可证校验失败：{archive.name}")
        for entry, target in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(entry) as source_file, target.open("xb") as destination_file:
                shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)
    if component["id"] == "python":
        # ._pth 隔离系统与用户安装目录；根目录相对于 vendor/python，不启用 site。
        # Windows 3.13 的 _pth 解析需要末尾分隔符，避免 ../.. 被规范化为单层父目录。
        (destination / "python313._pth").write_text("python313.zip\n.\n../../\n", encoding="utf-8", newline="\n")


def write_manifest(root: Path, *, version: str, lock: dict) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise BundleError("构建目录出现链接，拒绝归档")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if name == "SHA256SUMS.json":
                raise BundleError("源代码不得预置生成后的 SHA256SUMS.json")
            files[name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    manifest = {"schema": 1, "version": version, "platform": "windows-x64", "files": files,
                "manifest_excluded_from_own_hash": "SHA256SUMS.json",
                "dependencies": [{"id": item["id"], "version": item["version"],
                                   "archive_sha256": item["archive_sha256"]} for item in lock["components"]]}
    (root / "SHA256SUMS.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def write_zip(root: Path, output: Path) -> None:
    partial = output.with_suffix(".zip.part")
    if partial.exists():
        raise BundleError("成品存在未完成的 .part 文件，请检查后移除")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                info = zipfile.ZipInfo(root.name + "/" + path.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        partial.replace(output)
    except Exception:
        if partial.is_file():
            partial.unlink()
        raise


def build_bundle(source: Path, *, offline: bool = False, include_wechat: bool = False,
                 profile: str = "production") -> dict:
    source = source.resolve()
    files = collect_files(source, _tracked(source))
    try:
        version = files["VERSION"].decode("utf-8-sig").strip()
        lock = validate_lock(files["dependencies/windows-x64.lock.json"])
    except KeyError as exc:
        raise BundleError("VERSION 与依赖锁文件必须先加入 Git 跟踪白名单") from exc
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise BundleError("VERSION 必须是三段数字版本号")
    if profile not in {"production", "lantern"}:
        raise BundleError("运行包用途无效")
    launcher = f"启动{'彩灯' if profile == 'lantern' else '生产'}看板_V{version}.cmd"
    if launcher not in files:
        raise BundleError("缺少与 VERSION 一致的已跟踪启动入口")
    cache = _safe_path(source, "runtime/dependency_cache")
    archives = [(item, obtain_archive(item, cache, offline=offline)) for item in lock["components"]]
    temp_parent = _safe_path(source, "runtime/portable_build")
    temp_parent.mkdir(parents=True, exist_ok=True)
    output_dir = _safe_path(source, f"outputs/V{version}")
    output_dir.mkdir(parents=True, exist_ok=True)
    basename = f"{'lantern' if profile == 'lantern' else 'production'}-dashboard-V{version}-{'full-' if include_wechat else ''}windows-x64"
    output = _safe_path(output_dir, basename + ".zip")
    with tempfile.TemporaryDirectory(prefix="build_", dir=temp_parent) as temporary:
        root = Path(temporary) / basename
        root.mkdir()
        for name, data in files.items():
            destination = _safe_path(root, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        if profile == "lantern":
            guide = files.get("docs/lantern-start-here.txt")
            if guide is None:
                raise BundleError("缺少彩灯快速部署说明")
            (root / "开始使用_彩灯.txt").write_bytes(guide)
        for item, archive in archives:
            extract_component(archive, item, root)
        if profile == "lantern":
            try:
                try:
                    from scripts.build_document_runtime import build_document_runtime
                except ModuleNotFoundError:
                    from build_document_runtime import build_document_runtime
                build_document_runtime(root, cache / "documents", source, offline=offline)
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                raise BundleError("报关资料运行组件构建失败，请检查锁文件与缓存") from exc
        if include_wechat:
            try:
                from scripts.bundle_wechat_runtime import build_wechat_runtime, refresh_installation_records
                from scripts.build_media_runtime import build_media_runtime
            except ModuleNotFoundError:
                from bundle_wechat_runtime import build_wechat_runtime, refresh_installation_records
                from build_media_runtime import build_media_runtime
            try:
                build_wechat_runtime(root, cache / "wechat", offline=offline, source=source)
                build_media_runtime(root, cache, offline=offline, source=source)
                if 'dependencies/cloudflared.lock.json' in files:
                    try:
                        from scripts.build_remote_runtime import build_remote_runtime
                    except ModuleNotFoundError:
                        from build_remote_runtime import build_remote_runtime
                    build_remote_runtime(root,cache/'cloudflared',source=source,offline=offline)
                refresh_installation_records(root)
            except RuntimeError as exc:
                raise BundleError("微信或媒体组件构建失败，请检查依赖锁、许可及离线缓存") from exc
        manifest = write_manifest(root, version=version, lock=lock)
        if include_wechat:
            manifest["flavor"] = "full-wechat-offline-asr"
            manifest["additional_locks"] = {
                name: {"sha256": hashlib.sha256(data).hexdigest()}
                for name, data in files.items()
                if name.startswith("dependencies/") and name.endswith(".lock.json")
                and name != "dependencies/windows-x64.lock.json"
            }
            (root / "SHA256SUMS.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        write_zip(root, output)
    digest = sha256_file(output)
    checksum = output.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="ascii", newline="\n")
    return {"version": version, "profile": profile, "flavor": "full" if include_wechat else "core", "archive": str(output), "sha256": digest,
            "bytes": output.stat().st_size, "files": len(manifest["files"]) + 1,
            "checksum": str(checksum)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--offline", action="store_true", help="只使用已校验的本机依赖缓存")
    parser.add_argument("--with-wechat", action="store_true", help="额外打包微信采集、媒体解码和离线语音模型")
    parser.add_argument("--profile", choices=("production", "lantern"), default="production", help="独立彩灯运行包使用lantern")
    args = parser.parse_args()
    try:
        result = build_bundle(args.workspace, offline=args.offline, include_wechat=args.with_wechat, profile=args.profile)
    except (BundleError, PublishError, OSError, ValueError, zipfile.BadZipFile) as exc:
        message = str(exc) if isinstance(exc, (BundleError, PublishError)) else "网络或文件处理失败，请检查官方源、缓存和磁盘权限"
        print(f"便携包构建失败：{message}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
