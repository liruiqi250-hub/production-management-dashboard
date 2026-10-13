"""从公开 SHA-256 锁定档构建独立微信采集与本地语音运行环境。"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile

try:
    from scripts.publish_github import PublishError, _relative_name, _safe_path
except ModuleNotFoundError:
    from publish_github import PublishError, _relative_name, _safe_path

ALLOWED_HOSTS = {"github.com", "codeload.github.com", "raw.githubusercontent.com",
                 "release-assets.githubusercontent.com", "objects.githubusercontent.com",
                 "files.pythonhosted.org"}
MODULES = {"cffi": "cffi", "colorama": "colorama", "comtypes": "comtypes",
           "cryptography": "cryptography", "imageio-ffmpeg": "imageio_ffmpeg",
           "numpy": "numpy", "pillow": "PIL.Image", "psutil": "psutil",
           "pycparser": "pycparser", "pyperclip": "pyperclip", "pywin32": "win32api",
           "sherpa-onnx-core": "sherpa_onnx._info", "sherpa-onnx": "sherpa_onnx",
           "soundfile": "soundfile", "typing-extensions": "typing_extensions",
           "uiautomation": "uiautomation", "wechatauto-replica": "wechatauto",
           "winsdk": "winsdk.windows.media.ocr", "zstandard": "zstandard"}
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXTRACTED = 2 * 1024 * 1024 * 1024
PYTHON_PTH = ".\nLib\nDLLs\nLib/site-packages\n../source/wechat-chat-export-1.3.4/\nimport site\n"


class WeChatBundleError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise WeChatBundleError("微信依赖只允许无凭据的公开 HTTPS 来源")


def validate_lock(data: bytes) -> dict:
    lock = json.loads(data.decode("utf-8-sig"))
    if (lock.get("schema") != 1 or lock.get("platform") != "windows-x64"
            or lock.get("python_version") != "3.12.15"
            or lock.get("runtime_target") != "vendor/wechat/python"
            or lock.get("exporter_target") != "vendor/wechat/source/wechat-chat-export-1.3.4"):
        raise WeChatBundleError("微信便携依赖锁版本、平台或布局无效")
    seen = set()
    for item in lock["components"]:
        ident = item["id"]
        if ident in seen or item.get("kind") not in {
                "python-tar", "wheel", "source-zip", "exporter-zip", "silk-zip", "model-tar", "license-file"}:
            raise WeChatBundleError("微信依赖含重复或未知组件类型")
        seen.add(ident)
        _url(item["url"])
        name = item["archive_filename"]
        if _relative_name(name) != PurePosixPath(name).name:
            raise WeChatBundleError("依赖缓存必须使用单层文件名")
        if (not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
                or not isinstance(item.get("archive_bytes"), int)
                or not 0 < item["archive_bytes"] <= MAX_ARCHIVE):
            raise WeChatBundleError("微信依赖缺少有效哈希或固定大小")
    if seen != (set(MODULES) | {"python", "wechat-chat-export", "rust-silk", "sensevoice", "sensevoice-license"}):
        raise WeChatBundleError("微信依赖锁必须包含 19 个运行包、Python、采集器、解码器及完整语音模型")
    return lock


def obtain_archive(item: dict, cache: Path, *, offline: bool = False) -> Path:
    target = _safe_path(cache, item["archive_filename"])
    if target.exists():
        if (not target.is_file() or target.stat().st_size != item["archive_bytes"]
                or sha256(target) != item["sha256"]):
            raise WeChatBundleError(f"微信依赖缓存校验失败：{target.name}")
        return target
    if offline:
        raise WeChatBundleError(f"离线缓存缺少：{target.name}")
    cache.mkdir(parents=True, exist_ok=True)
    temporary = _safe_path(cache, target.name + ".part")
    if temporary.exists():
        raise WeChatBundleError(f"发现未完成的下载：{temporary.name}")
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(item["url"], headers={"User-Agent": "ProductionDashboard-Portable/1"})
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open("xb") as out:
            _url(response.geturl())
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > item["archive_bytes"]:
                    raise WeChatBundleError(f"微信依赖下载超过固定大小：{target.name}")
                digest.update(chunk)
                out.write(chunk)
        if size != item["archive_bytes"] or digest.hexdigest() != item["sha256"]:
            raise WeChatBundleError(f"微信依赖下载校验失败：{target.name}")
        temporary.replace(target)
    finally:
        if temporary.is_file():
            temporary.unlink()
    return target


def _zip_entries(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    entries, seen, size = [], set(), 0
    for item in archive.infolist():
        name = _relative_name(item.filename.rstrip("/"))
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise WeChatBundleError("微信依赖 ZIP 含链接或特殊文件")
        if name.casefold() in seen:
            raise WeChatBundleError("微信依赖 ZIP 含重复文件")
        seen.add(name.casefold())
        size += item.file_size
        if size > MAX_EXTRACTED:
            raise WeChatBundleError("微信依赖 ZIP 解压大小超过上限")
        if not item.is_dir():
            entries.append((item, name))
    return entries


def _write_stream(stream, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as out:
        shutil.copyfileobj(stream, out, 1024 * 1024)


def _extract_python(archive: Path, target: Path, item: dict) -> None:
    with tarfile.open(archive, "r:gz") as source:
        plan, seen, total = [], set(), 0
        for entry in source.getmembers():
            name = _relative_name(entry.name.rstrip("/"))
            if entry.issym() or entry.islnk() or not (entry.isfile() or entry.isdir()):
                raise WeChatBundleError("Python 公开档含链接或特殊文件")
            if not name.startswith("python/"):
                raise WeChatBundleError("Python 公开档顶层目录无效")
            name = name.removeprefix("python/")
            if name.casefold() in seen:
                raise WeChatBundleError("Python 公开档含重复文件")
            seen.add(name.casefold())
            total += entry.size
            if total > MAX_EXTRACTED:
                raise WeChatBundleError("Python 公开档解压超过上限")
            # 原发行档自带 pip；运行包仅需要锁定的业务依赖，完全省略预装 site-packages。
            if entry.isfile() and not name.startswith("Lib/site-packages/"):
                plan.append((entry, _safe_path(target, name)))
        license_data = source.extractfile("python/LICENSE.txt").read()
        if hashlib.sha256(license_data).hexdigest() != item["license_sha256"]:
            raise WeChatBundleError("Python 原始许可校验失败")
        if "python.exe" not in seen or "python312.dll" not in seen:
            raise WeChatBundleError("Python 运行文件缺失")
        for entry, destination in plan:
            with source.extractfile(entry) as stream:
                _write_stream(stream, destination)
    (target / "python312._pth").write_text(PYTHON_PTH, encoding="utf-8", newline="\n")


def _wheel_target(name: str, python: Path) -> Path:
    parts = PurePosixPath(name).parts
    if parts[0].endswith(".data"):
        if len(parts) < 3:
            raise WeChatBundleError("wheel .data 目录无效")
        category, relative = parts[1], "/".join(parts[2:])
        if category in {"purelib", "platlib"}:
            return _safe_path(python, "Lib/site-packages/" + relative)
        if category == "scripts":
            return _safe_path(python, "Scripts/" + relative)
        if category == "data":
            return _safe_path(python, relative)
        raise WeChatBundleError("wheel 含不支持的安装类别")
    return _safe_path(python, "Lib/site-packages/" + name)


def _extract_wheel(archive: Path, python: Path, item: dict) -> None:
    with zipfile.ZipFile(archive) as source:
        plan = []
        for entry, name in _zip_entries(source):
            # imageio Windows wheel 的旧 FFmpeg 是 GPL build；保留 BSD wrapper/许可，绝不写出二进制。
            if item["id"] == "imageio-ffmpeg" and (name.startswith("imageio_ffmpeg/binaries/")
                    and Path(name).suffix.lower() in {".exe", ".dll"}):
                continue
            if name.endswith(".pth") and not (item["id"] == "pywin32" and name == "pywin32.pth"):
                raise WeChatBundleError("wheel 含未经允许的启动 .pth")
            plan.append((entry, _wheel_target(name, python)))
        for entry, target in plan:
            with source.open(entry) as stream:
                _write_stream(stream, target)


def _extract_wechatauto(archive: Path, python: Path, item: dict) -> None:
    site = _safe_path(python, "Lib/site-packages")
    prefix = "wechatauto-replica-04ef8cbde3862cff90b5f6b42c9ebfcea44ef48d/"
    dist = _safe_path(site, "wechatauto_replica-1.1.9.dist-info")
    with zipfile.ZipFile(archive) as source:
        entries = _zip_entries(source)
        for entry, name in entries:
            if not name.startswith(prefix):
                raise WeChatBundleError("wechatauto 固定源码顶层目录不匹配")
        selected = [(entry, name[len(prefix):]) for entry, name in entries
                    if name[len(prefix):].startswith("wechatauto/")]
        if not any(name == "wechatauto/__init__.py" for _, name in selected):
            raise WeChatBundleError("wechatauto 源码缺少包入口")
        for entry, name in selected:
            with source.open(entry) as stream:
                _write_stream(stream, _safe_path(site, name))
        dist.mkdir()
        _safe_path(dist, "LICENSE").write_bytes(source.read(prefix + "LICENSE"))
        _safe_path(dist, "pyproject.toml").write_bytes(source.read(prefix + "pyproject.toml"))
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: wechatauto-replica\nVersion: 1.1.9\nLicense: Apache-2.0\n", encoding="utf-8")
    (dist / "INSTALLER").write_text("production-dashboard-locked-source\n", encoding="utf-8")
    (dist / "direct_url.json").write_text(json.dumps({"url": item["url"], "archive_info": {
        "hashes": {"sha256": item["sha256"]}}}, indent=2) + "\n", encoding="utf-8")


def _extract_selected_zip(archive: Path, target: Path, expected: dict[str, str]) -> None:
    with zipfile.ZipFile(archive) as source:
        entries = _zip_entries(source)
        selected = {}
        for name, digest in expected.items():
            matches = [entry for entry, relative in entries if PurePosixPath(relative).name == name]
            if len(matches) != 1:
                raise WeChatBundleError("锁定的采集器/解码器文件缺失或不唯一")
            data = source.read(matches[0])
            if hashlib.sha256(data).hexdigest() != digest:
                raise WeChatBundleError("采集器/解码器内部文件哈希不匹配")
            selected[name] = data
        for name, data in selected.items():
            destination = _safe_path(target, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def _extract_model(archive: Path, model: Path, expected: dict[str, str]) -> None:
    with tarfile.open(archive, "r:bz2") as source:
        entries = source.getmembers()
        for entry in entries:
            _relative_name(entry.name.rstrip("/"))
            if not (entry.isfile() or entry.isdir()):
                raise WeChatBundleError("语音模型压缩包含链接或特殊文件")
        for name, digest in expected.items():
            matches = [entry for entry in entries if PurePosixPath(entry.name).name == name and entry.isfile()]
            if len(matches) != 1 or matches[0].size > 300 * 1024 * 1024:
                raise WeChatBundleError("锁定的模型或词表文件缺失")
        for name, digest in expected.items():
            entry = next(entry for entry in entries if PurePosixPath(entry.name).name == name and entry.isfile())
            target = _safe_path(model, name)
            with source.extractfile(entry) as stream:
                _write_stream(stream, target)
            if sha256(target) != digest:
                raise WeChatBundleError("语音模型或词表内部 SHA-256 不匹配")


def build_wechat_runtime(root: Path, cache: Path, *, offline: bool = False,
                         source: Path | None = None) -> dict:
    """root 为全新便携包根目录，cache 为公开组件缓存；不读取任何本机微信资料。"""
    source = (source or Path(__file__).resolve().parents[1]).resolve()
    lock = validate_lock((source / "dependencies/wechat-portable.lock.json").read_bytes())
    runtime = _safe_path(root, "vendor/wechat")
    if runtime.exists():
        raise WeChatBundleError("微信便携构建目标必须全新，拒绝覆盖已有资料")
    archives = {item["id"]: obtain_archive(item, cache, offline=offline) for item in lock["components"]}
    python = _safe_path(root, lock["runtime_target"])
    exporter = _safe_path(root, lock["exporter_target"])
    model = _safe_path(exporter, "tools/asr/sensevoice")
    for item in lock["components"]:
        archive, kind = archives[item["id"]], item["kind"]
        if kind == "python-tar":
            _extract_python(archive, python, item)
        elif kind == "wheel":
            _extract_wheel(archive, python, item)
        elif kind == "source-zip":
            _extract_wechatauto(archive, python, item)
        elif kind == "exporter-zip":
            _extract_selected_zip(archive, exporter, item["files"])
        elif kind == "silk-zip":
            _extract_selected_zip(archive, _safe_path(exporter, "tools"), {"rust-silk.exe": item["file_sha256"]})
            _extract_selected_zip(archive, _safe_path(exporter, "tools/rust-silk-license"), item["license_files"])
        elif kind == "model-tar":
            _extract_model(archive, model, item["files"])
        elif kind == "license-file":
            target = _safe_path(model, "MODEL_LICENSE.txt")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, target)
    model_info = next(item for item in lock["components"] if item["id"] == "sensevoice")
    (model / "MODEL_SOURCE.json").write_text(json.dumps(model_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    notice = ("微信采集与语音运行环境\n\n全部组件来自 dependencies/wechat-portable.lock.json 的公开固定下载档。\n"
              "Python、Python wheels、wechatauto 和采集器的原许可证保留在各自目录。\n"
              "imageio-ffmpeg 仅保留 BSD wrapper；原 wheel 中的 GPL FFmpeg 二进制已排除，替代组件另列来源许可。\n"
              "Sherpa-ONNX 的原 Python/source 许可为 Apache-2.0，其 native ASR/TTS 库静态包含 GPL 的 espeak-ng；整体 native 组件的额外许可与对应源码见媒体许可清单及 source companion。\n"
              "模型采用 FunASR Model Open Source License Agreement v1.1，完整条款见 source/.../tools/asr/sensevoice/MODEL_LICENSE.txt。\n"
              "未包含微信客户端、账号密钥、聊天原文、媒体、业务数据库或用户缓存。\n")
    (runtime / "THIRD_PARTY_NOTICES.txt").write_text(notice, encoding="utf-8")
    (runtime / "COMPONENTS.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    refresh_installation_records(root)
    return {"components": len(lock["components"]), "python": lock["python_version"],
            "packages": len(MODULES), "model": "SenseVoice Small Int8 2024-07-17",
            "runtime": "vendor/wechat", "connected_to_wechat": False,
            "artifacts": [{"id": item["id"], "sha256": item["sha256"]} for item in lock["components"]]}


def refresh_installation_records(root: Path) -> dict:
    """wheel 安装、移除旧 FFmpeg 与替换媒体 DLL 后刷新 RECORD；最终须在 media 构建后再调用。"""
    python = _safe_path(root, "vendor/wechat/python")
    site = _safe_path(python, "Lib/site-packages")
    result = {}
    for dist in sorted(site.glob("*.dist-info")):
        record = _safe_path(site, dist.name + "/RECORD")
        targets = set()
        if record.is_file():
            with record.open(encoding="utf-8", newline="") as stream:
                for row in csv.reader(stream):
                    if not row:
                        continue
                    name = row[0]
                    path = PurePosixPath(name)
                    if path.is_absolute() or "\\" in name or ":" in name:
                        raise WeChatBundleError("wheel RECORD 含无效路径")
                    if ".." in path.parts:
                        # 已安装 wheel 的脚本用相对 site-packages 的 ../../Scripts 路径记录。
                        candidate = site.joinpath(*path.parts).resolve()
                        if not candidate.is_relative_to(python.resolve()):
                            raise WeChatBundleError("wheel RECORD 路径超出包内 Python")
                        target = _safe_path(python, candidate.relative_to(python.resolve()).as_posix())
                    else:
                        target = _wheel_target(_relative_name(name), python)
                    if target.is_file():
                        targets.add(target)
        elif dist.name == "wechatauto_replica-1.1.9.dist-info":
            targets.update(p for p in _safe_path(site, "wechatauto").rglob("*") if p.is_file())
            targets.update(p for p in dist.rglob("*") if p.is_file())
        else:
            raise WeChatBundleError("运行 wheel 缺少 RECORD 清单")
        targets.add(record)
        rows = []
        for path in sorted(targets):
            name = Path(os.path.relpath(path, site)).as_posix()
            if path == record:
                rows.append((name, "", ""))
            else:
                checked = _safe_path(python, path.relative_to(python).as_posix())
                digest = base64.urlsafe_b64encode(bytes.fromhex(sha256(checked))).rstrip(b"=").decode("ascii")
                rows.append((name, "sha256=" + digest, str(checked.stat().st_size)))
        with record.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, lineterminator="\n").writerows(rows)
        result[dist.name] = len(rows)
    adjustments = {"schema": 1, "record_policy": "RECORD hashes describe installed files; the root SHA256SUMS.json is authoritative for the complete package.",
                   "removed": ["imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe (upstream GPL build excluded)"],
                   "replacements": "LGPL-only FFmpeg and minimal libsndfile are built separately from pinned source; see media component lock and source companion.",
                   "unchanged": "Original component licenses are retained without modification.", "distributions": result}
    (_safe_path(root, "vendor/wechat") / "INSTALLATION_ADJUSTMENTS.json").write_text(
        json.dumps(adjustments, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def probe_runtime(root: Path, *, test_model: bool = False) -> dict:
    """只导入本包组件、可选静音模型推理；空缓存环境，绝不连接微信或读取绑定。"""
    runtime = _safe_path(root, "vendor/wechat")
    python = _safe_path(runtime, "python/python.exe")
    program = r'''
import importlib, importlib.metadata as md, json, os, pathlib, site, sys
runtime = pathlib.Path(sys.executable).resolve().parents[1]
modules = json.loads(sys.argv[1])
installed = {}
for name, module in modules.items():
    importlib.import_module(module)
    installed[name] = md.version(name)
import exporter_core as core
if not sys.flags.isolated or not sys.flags.ignore_environment or site.ENABLE_USER_SITE:
    raise RuntimeError('Python isolation policy failed')
paths = [pathlib.Path(p).resolve() for p in sys.path]
if any(not p.is_relative_to(runtime) for p in paths):
    raise RuntimeError('sys.path contains external installation')
model, tokens = core._find_sensevoice_model()
expected = runtime / 'source/wechat-chat-export-1.3.4/tools/asr/sensevoice'
if pathlib.Path(model).resolve() != expected / 'model.int8.onnx' or pathlib.Path(tokens).resolve() != expected / 'tokens.txt':
    raise RuntimeError('ASR model did not resolve to portable package')
result = dict(version=sys.version.split()[0], isolated=sys.flags.isolated,
    ignore_environment=sys.flags.ignore_environment, user_site=site.ENABLE_USER_SITE,
    sys_path=[str(p.relative_to(runtime)) for p in paths], packages=installed,
    exporter_imported=True, model_path=str(pathlib.Path(model).relative_to(runtime)),
    wechat_connected=False, model_inference=False)
if sys.argv[2] == '1':
    import numpy as np, sherpa_onnx
    recognizer=sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model),tokens=str(tokens),num_threads=2,use_itn=True,debug=False)
    stream=recognizer.create_stream()
    stream.accept_waveform(16000,np.zeros(16000,dtype=np.float32))
    recognizer.decode_stream(stream)
    result['model_inference']=True
print(json.dumps(result,ensure_ascii=False))
'''
    with tempfile.TemporaryDirectory(prefix="wechat_portable_probe_") as temporary:
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "DINO_WECHAT_RUNTIME", "DINO_WECHAT_BINDING", "IMAGEIO_FFMPEG_EXE"):
            env.pop(key, None)
        env.update(APPDATA=temporary, LOCALAPPDATA=temporary, USERPROFILE=temporary,
                   TEMP=temporary, TMP=temporary, PYTHONDONTWRITEBYTECODE="1")
        replacement = _safe_path(runtime, "ffmpeg/bin/ffmpeg.exe")
        if replacement.is_file():
            env["IMAGEIO_FFMPEG_EXE"] = str(replacement)
        try:
            result = subprocess.run([str(python), "-I", "-B", "-c", program,
                                     json.dumps(MODULES), "1" if test_model else "0"],
                                    cwd=temporary, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=180,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WeChatBundleError("包内微信环境诊断启动失败或超时") from exc
        if result.returncode:
            # 仅为离线公开包导入，调用者可在本机保存 stderr 排障；不读取任何业务资料。
            raise WeChatBundleError("包内微信环境导入或模型测试失败：" + result.stderr[-2000:])
        return json.loads(result.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="全新便携包目标根目录")
    parser.add_argument("--cache", type=Path, default=Path(__file__).resolve().parents[1] / "runtime/dependency_cache/wechat")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--probe", action="store_true", help="构建后导入组件并测试包内语音模型")
    args = parser.parse_args()
    try:
        result = build_wechat_runtime(args.root, args.cache, offline=args.offline, source=args.source)
        if args.probe:
            result["probe"] = probe_runtime(args.root, test_model=True)
    except (WeChatBundleError, PublishError, OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
