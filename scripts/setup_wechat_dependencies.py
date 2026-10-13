"""可选微信采集环境：默认只诊断，--install 才下载公开依赖到本项目。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile


WORKSPACE = Path(__file__).resolve().parents[1]
LOCK_PATH = WORKSPACE / "dependencies" / "wechat-collector.lock.json"
ALLOWED_HOSTS = {"files.pythonhosted.org", "github.com", "codeload.github.com",
                 "raw.githubusercontent.com", "release-assets.githubusercontent.com",
                 "objects.githubusercontent.com"}
PYTHON_PROBE = """
import importlib.metadata as m, importlib.util, json, os, platform, struct, sys
packages={}
for d in m.distributions():
 name=d.metadata.get('Name','').lower().replace('_','-').replace('.','-')
 packages[name]={'version':d.version}
 if name=='wechatauto-replica':
  try:packages[name]['direct_url']=json.loads(d.read_text('direct_url.json') or '{}')
  except (ValueError,TypeError):pass
print(json.dumps({'version':'.'.join(map(str,sys.version_info[:2])),
 'platform':os.name,'bits':struct.calcsize('P')*8,'venv':bool(importlib.util.find_spec('venv')),
 'ensurepip':bool(importlib.util.find_spec('ensurepip')),'packages':packages}))
"""


class SetupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def safe_path(*parts: str) -> Path:
    """固定写入 vendor/wechat，拒绝目录链接、junction 和越界。"""
    if any(PurePosixPath(part.replace("\\", "/")).is_absolute()
           or ".." in PurePosixPath(part.replace("\\", "/")).parts or ":" in part
           for part in parts):
        raise SetupError("依赖目标包含绝对路径或上级目录。")
    candidate = WORKSPACE.joinpath("vendor", "wechat", *parts)
    current = WORKSPACE
    for part in candidate.relative_to(WORKSPACE).parts:
        current /= part
        if current.exists() or current.is_symlink():
            details = current.lstat()
            if current.is_symlink() or getattr(details, "st_file_attributes", 0) & 0x400:
                raise SetupError("安装目录包含链接或junction，拒绝写入。")
    resolved = candidate.resolve()
    expected = WORKSPACE / "vendor" / "wechat"
    if not resolved.is_relative_to(expected):
        raise SetupError("安装目录超出项目 vendor/wechat。")
    if resolved == expected and parts:
        raise SetupError("安装子目录无效。")
    return resolved


def checked_target(path: Path) -> Path:
    try:
        parts = path.relative_to(WORKSPACE / "vendor" / "wechat").parts
    except ValueError as exc:
        raise SetupError("依赖目标超出项目安装目录。") from exc
    return safe_path(*parts)


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise SetupError("依赖URL不在锁定的公开HTTPS来源白名单内。")


def load_lock() -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1 or lock.get("platform") != "windows-x64":
        raise SetupError("依赖锁格式或平台不匹配。")
    for artifact in [lock["exporter"], lock["rust_silk"],
                     *lock["packages"], *lock["build_packages"]]:
        validate_url(artifact["url"])
        if not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
            raise SetupError("依赖缺少有效SHA-256锁。")
    validate_url(lock["sensevoice"]["url"])
    validate_url(lock["sensevoice"]["license_url"])
    return lock


def run_python(python: Path, arguments: list[str], timeout: int = 120) -> str:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"):
        env.pop(key, None)
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1",
               PIP_NO_INDEX="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        result = subprocess.run([str(python), "-I", "-B", *arguments],
                                cwd=WORKSPACE, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError("Python调用失败或超时；请核对完整Python环境。") from exc
    if result.returncode:
        raise SetupError("Python依赖操作失败；未连接微信，未修改原业务环境。")
    return result.stdout.strip()


def probe(python: Path) -> dict:
    return json.loads(run_python(python, ["-c", PYTHON_PROBE]))


def validate_python(info: dict, *, needs_venv: bool = False) -> None:
    if info["platform"] != "nt" or info["bits"] != 64 or info["version"] != "3.12":
        raise SetupError("微信采集依赖仅支持Windows x64 Python 3.12；请通过--python指定带venv和ensurepip的完整Python 3.12。")
    if needs_venv and (not info["venv"] or not info["ensurepip"]):
        raise SetupError("需要带venv和ensurepip的完整Python 3.12；可用--python指定。")


def package_status(info: dict, packages: list[dict]) -> dict:
    status = {}
    for item in packages:
        name = re.sub(r"[-_.]+", "-", item["name"].lower())
        installed = info["packages"].get(name, {})
        matches = installed.get("version") == item["version"]
        if name == "wechatauto-replica" and matches:
            archive = installed.get("direct_url", {}).get("archive_info", {})
            matches = (archive.get("hashes", {}).get("sha256") == item["sha256"]
                       or archive.get("hash") == "sha256=" + item["sha256"])
        status[item["name"]] = {"expected": item["version"],
                                "installed": installed.get("version"), "verified": matches}
    return status


def files_match(root: Path, expected: dict[str, str]) -> bool:
    return all(checked_target(root / name).is_file() and sha256(root / name) == digest
               for name, digest in expected.items())


def download(url: str, target: Path, expected_hash: str | None,
             max_bytes: int = 512 * 1024 * 1024) -> Path:
    validate_url(url)
    target = checked_target(target)
    if target.is_file() and expected_hash and sha256(target) == expected_hash:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = checked_target(target.with_name(target.name + ".partial"))
    request = urllib.request.Request(url, headers={"User-Agent": "production-dashboard-setup"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as out:
            validate_url(response.url)
            length = 0
            for block in iter(lambda: response.read(1024 * 1024), b""):
                length += len(block)
                if length > max_bytes:
                    raise SetupError("下载文件超过本项允许大小。")
                out.write(block)
        if expected_hash and sha256(temporary) != expected_hash:
            raise SetupError("公开依赖SHA-256校验失败，拒绝安装。")
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def archive_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in name:
        raise SetupError("公开压缩包包含不安全路径。")
    return path


def copy_zip_files(archive: Path, target: Path, expected: dict[str, str]) -> None:
    with zipfile.ZipFile(archive) as source:
        entries = {}
        for item in source.infolist():
            relative = archive_member(item.filename)
            if stat.S_ISLNK(item.external_attr >> 16):
                raise SetupError("公开压缩包包含链接，拒绝解压。")
            if relative.name in expected:
                entries.setdefault(relative.name, []).append(item)
        for name, digest in expected.items():
            found = entries.get(name, [])
            if len(found) != 1 or found[0].file_size > 16 * 1024 * 1024:
                raise SetupError("公开压缩包的锁定文件缺失或不唯一。")
            content = source.read(found[0])
            if hashlib.sha256(content).hexdigest() != digest:
                raise SetupError("公开压缩包内源码校验失败。")
            target.mkdir(parents=True, exist_ok=True)
            checked_target(target / name).write_bytes(content)


def install_model(lock: dict) -> None:
    item = lock["sensevoice"]
    model_root = safe_path("source", "wechat-chat-export-1.3.4", "tools", "asr", "sensevoice")
    if not files_match(model_root, item["files"]):
        archive = download(item["url"], safe_path("downloads", "sensevoice.tar.bz2"), None)
        with tarfile.open(archive, "r:bz2") as source:
            expected = item["files"]
            entries = {}
            for member in source.getmembers():
                name = archive_member(member.name).name
                if member.issym() or member.islnk():
                    raise SetupError("模型压缩包包含链接。")
                if name in expected:
                    entries.setdefault(name, []).append(member)
            for name, digest in expected.items():
                matches = entries.get(name, [])
                if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 300 * 1024 * 1024:
                    raise SetupError("模型文件缺失或不唯一。")
                stream = source.extractfile(matches[0])
                if stream is None:
                    raise SetupError("无法读取模型文件。")
                data = stream.read()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise SetupError("模型或词表SHA-256校验失败。")
                model_root.mkdir(parents=True, exist_ok=True)
                checked_target(model_root / name).write_bytes(data)
    download(item["license_url"], model_root / "MODEL_LICENSE.txt", item["license_sha256"], 1024 * 1024)
    checked_target(model_root / "MODEL_SOURCE.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="显式下载并安装到本项目vendor/wechat")
    parser.add_argument("--with-model", action="store_true", help="安装时同时下载约240MB本地语音模型")
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="完整Windows x64 Python 3.12")
    args = parser.parse_args()
    if args.with_model and not args.install:
        raise SetupError("--with-model必须与--install一起使用；默认诊断不下载。")
    lock = load_lock()
    runtime = safe_path()
    source_root = safe_path("source", "wechat-chat-export-1.3.4")
    installed_python = safe_path("venv", "Scripts", "python.exe")
    info = probe(installed_python if installed_python.is_file() else args.python)
    validate_python(info, needs_venv=args.install and not installed_python.is_file())
    status = package_status(info, lock["packages"])
    if args.install:
        if not installed_python.is_file():
            run_python(args.python, ["-m", "venv", str(safe_path("venv"))])
        status = package_status(probe(installed_python), lock["packages"])
        if not files_match(source_root, lock["exporter"]["files"]):
            exporter = download(lock["exporter"]["url"], safe_path("downloads", "exporter-v1.3.4.zip"), lock["exporter"]["sha256"])
            copy_zip_files(exporter, source_root, lock["exporter"]["files"])
        if not all(item["verified"] for item in status.values()) or not (runtime / "venv").is_dir():
            artifacts = []
            for item in [*lock["build_packages"], *lock["packages"]]:
                name = urllib.parse.unquote(urllib.parse.urlsplit(item["url"]).path.rsplit("/", 1)[-1])
                artifact = download(item["url"], safe_path("downloads", name), item["sha256"])
                artifacts.append(artifact)
            for artifact in artifacts:
                run_python(installed_python, ["-m", "pip", "install", "--no-index", "--no-deps", "--no-build-isolation", str(artifact)], timeout=300)
        rust = lock["rust_silk"]
        tools = safe_path("source", "wechat-chat-export-1.3.4", "tools")
        if not files_match(tools, {"rust-silk.exe": rust["file_sha256"]}):
            archive = download(rust["url"], safe_path("downloads", "rust-silk-v0.1.3.zip"), rust["sha256"])
            copy_zip_files(archive, tools, {"rust-silk.exe": rust["file_sha256"]})
        if args.with_model:
            install_model(lock)
        info = probe(installed_python)
        validate_python(info)
        status = package_status(info, lock["packages"])
        if not all(item["verified"] for item in status.values()):
            raise SetupError("安装后存在版本或来源不匹配，未连接微信。")
        run_python(installed_python, ["-m", "pip", "check"])
    result = {"mode": "installed" if args.install else "diagnostic_only",
              "runtime": str(runtime), "python": str(installed_python if installed_python.is_file() else args.python),
              "source_verified": files_match(source_root, lock["exporter"]["files"]),
              "packages": status,
              "rust_silk_verified": files_match(source_root / "tools", {"rust-silk.exe": lock["rust_silk"]["file_sha256"]}),
              "model_verified": files_match(source_root / "tools" / "asr" / "sensevoice", lock["sensevoice"]["files"]),
              "wechat_connected": False, "binding_read": False,
              "next_step": "私有账号绑定步骤见dependencies/微信采集依赖.md"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SetupError, OSError, ValueError, KeyError, zipfile.BadZipFile, tarfile.TarError) as exc:
        message = str(exc) if isinstance(exc, SetupError) else "依赖准备失败；请检查网络、文件权限和完整Python环境。"
        print(message, file=sys.stderr)
        raise SystemExit(2)
