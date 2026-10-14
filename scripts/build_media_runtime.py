"""部署已核验的独立媒体共享库；编译器与本机缓存不进入运行包。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import zipfile

try:
    from scripts.publish_github import PublishError, _relative_name, _safe_path
except ModuleNotFoundError:
    from publish_github import PublishError, _relative_name, _safe_path


REPOSITORY = "liruiqi250-hub/production-management-dashboard"


class MediaBuildError(RuntimeError):
    """媒体依赖缺失、损坏或路径不安全。"""


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _cached(cache: Path, component: dict, *, offline: bool) -> Path:
    filename = _relative_name(component["archive_filename"])
    if "/" in filename or not filename.endswith(".zip"):
        raise MediaBuildError("媒体依赖归档名无效")
    path = _safe_path(cache, filename)
    if not path.exists():
        if offline:
            raise MediaBuildError(f"离线缓存缺少媒体依赖：{filename}")
        gh = shutil.which("gh") or "C:/Program Files/GitHub CLI/gh.exe"
        try:
            subprocess.run(
                [gh, "release", "download", component["release_tag"], "--repo", REPOSITORY,
                 "--pattern", filename, "--dir", str(cache)],
                check=True, capture_output=True, timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MediaBuildError(
                f"无法取得媒体依赖 {filename}；请先登录有此私有仓库权限的 GitHub CLI，"
                "或下载该 Release 附件到 runtime/dependency_cache/media 后离线构建"
            ) from exc
    if path.is_symlink() or not path.is_file() or _sha(path) != component["archive_sha256"]:
        raise MediaBuildError(f"媒体依赖 SHA-256 不符：{filename}")
    return path


def _extract_verified(archive: Path, destination: Path, files: dict[str, str]) -> None:
    _safe_path(destination, "preflight")
    limit = 150 * 1024 * 1024
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if sum(x.file_size for x in entries) > limit:
            raise MediaBuildError("媒体依赖归档解压大小超出上限")
        actual, content = set(), {}
        for entry in entries:
            if entry.is_dir():
                continue
            name = _relative_name(entry.filename)
            parts = PurePosixPath(name).parts
            if (not parts or "\\" in name or name.startswith("/") or ":" in name
                    or any(p in ("", ".", "..") for p in parts)
                    or stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK
                    or name in actual or name not in files):
                raise MediaBuildError("媒体依赖归档含未登记或不安全路径")
            _safe_path(destination, name)
            actual.add(name)
            data = source.read(entry)
            if hashlib.sha256(data).hexdigest() != files[name]:
                raise MediaBuildError(f"媒体文件 SHA-256 不符：{name}")
            content[name] = data
        if actual != set(files):
            raise MediaBuildError("媒体依赖归档缺少登记文件")
    # 全部路径与字节通过预检之后才写入，损坏归档不覆盖现有 DLL。
    for name, data in content.items():
        target = _safe_path(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def build_media_runtime(root: Path, cache: Path, *, offline: bool = False,
                        source: Path | None = None) -> dict:
    """在便携根目录部署 FFmpeg 与最小 libsndfile，返回待上传的公开来源资产。"""
    root, cache = Path(root).absolute(), Path(cache).absolute()
    _safe_path(root, "vendor/wechat/ffmpeg/bin/ffmpeg.exe")
    cache = _safe_path(cache, "media")
    cache.mkdir(parents=True, exist_ok=True)
    source_root = Path(source) if source else Path(__file__).resolve().parents[1]
    lock_path = _safe_path(source_root, "dependencies/media-runtime.lock.json")
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    if lock.get("schema") != 1 or lock.get("platform") != "windows-x64":
        raise MediaBuildError("媒体依赖锁文件版本或平台无效")
    assets = []
    for component in lock["components"]:
        archive = _cached(cache, component, offline=offline)
        if component["id"] == "ffmpeg":
            target = root / "vendor/wechat/ffmpeg"
        elif component["id"] == "libsndfile":
            target = root / "vendor/wechat/python/Lib/site-packages/_soundfile_data"
        else:
            raise MediaBuildError("媒体依赖锁文件包含未知组件")
        if not target.resolve().is_relative_to(root):
            raise MediaBuildError("媒体依赖安装目录越界")
        _extract_verified(archive, target, component["files"])
        assets.append({"path": str(archive), "filename": archive.name,
                       "size": archive.stat().st_size, "sha256": _sha(archive)})
    source = _cached(cache, lock["corresponding_sources"], offline=offline)
    assets.append({"path": str(source), "filename": source.name,
                   "size": source.stat().st_size, "sha256": _sha(source)})
    licenses = root / "licenses/media"
    licenses.mkdir(parents=True, exist_ok=True)
    origin = lock_path.parent / "media"
    for name in ("THIRD_PARTY_NOTICES.md", "BUILD.md", "SOURCE_MANIFEST.json",
                 "Sherpa-ONNX-LICENSE.txt", "ONNXRuntime-LICENSE.txt", "ONNXRuntime-ThirdPartyNotices.txt",
                 "LLVM-RUNTIME-LICENSE.txt", "MinGW-RUNTIME-LICENSE.txt", "Espeak-NG-COPYING.txt",
                 "Espeak-NG-COPYING.APACHE.txt", "Espeak-NG-COPYING.BSD2.txt", "Espeak-NG-COPYING.UCD.txt"):
        source_file = origin / name
        if not source_file.is_file():
            raise MediaBuildError(f"缺少媒体许可或来源材料：{name}")
        shutil.copy2(source_file, licenses / name)
    shutil.copy2(lock_path, licenses / lock_path.name)
    third_party = origin / "third-party"
    if third_party.is_dir():
        for source_file in sorted(third_party.rglob("*.txt")):
            name = source_file.relative_to(origin).as_posix()
            _safe_path(origin, name)
            target = _safe_path(licenses, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
    return {"ffmpeg": "vendor/wechat/ffmpeg/bin/ffmpeg.exe",
            "libsndfile": "vendor/wechat/python/Lib/site-packages/_soundfile_data/libsndfile_x64.dll",
            "license": "LGPL-2.1-or-later", "assets": assets}
