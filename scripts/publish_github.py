"""将已提交的代码白名单发布到独立 GitHub checkout，默认只准备本地提交。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterable


DEFAULT_REPO = "https://github.com/liruiqi250-hub/production-management-dashboard"
PROJECT_REPOS = {
    "production": DEFAULT_REPO,
    "lantern": "https://github.com/liruiqi250-hub/lantern-management-dashboard",
}
TEXT_SUFFIXES = {".py", ".js", ".cjs", ".mjs", ".ts", ".css", ".html", ".json", ".md", ".txt",
                 ".ps1", ".cmd", ".bat", ".toml", ".yaml", ".yml", ".csv",
                 ".ini", ".cfg", ".xml", ".sh", ".cmake"}
ROOT_FILES = {".gitignore", "AGENTS.md", "README.md", "CHANGELOG.md", "VERSION", "PROJECT.json", "INSTALL_FOR_CODEX.md",
              "检查完整安装.cmd", "配置微信采集.cmd"}
CODE_DIRS = {"app", "web", "scripts", "tests", "docs", "automation", "dependencies"}
EXCLUDED_DIRS = {"outputs", "private", "acquisition", "analysis", "runtime",
                 "node_modules", "__pycache__", ".git"}
SENSITIVE_NAME = re.compile(r"(?:^|[._-])(?:private|secrets?|tokens?|credentials?)(?:$|[._-])", re.I)
RESERVED_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|glpat-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\b(?:https?://)[^\s/@:]+:[^\s/@]+@", re.I),
)
HARDCODED_SECRET = re.compile(
    r"\b(?:api[_-]?key|(?:access[_-]?|auth[_-]?|api[_-]?)?token|(?:client[_-]?)?secret)"
    r"\s*[:=]\s*['\"]([^'\"\r\n]{12,})['\"]", re.I)
ENV_SECRET = re.compile(
    r"^\s*(?:export\s+)?[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY)[A-Z0-9_]*"
    r"\s*=\s*([A-Za-z0-9_+/.=-]{12,})\s*$", re.M)
PLACEHOLDER = re.compile(r"(?:example|placeholder|changeme|dummy|your[_ -]|test[_ -]|x{4,}|<|\$\{|%[A-Z_]+%)", re.I)


class PublishError(RuntimeError):
    """错误信息仅含操作和文件名，不包含命令输出或文件内容。"""


def run(args: list[str], *, cwd: Path, allowed: tuple[int, ...] = (0,),
        timeout: int = 90) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
    try:
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as exc:
        raise PublishError(f"未找到所需程序：{args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"操作超时：{args[0]}") from exc
    if result.returncode not in allowed:
        # Git/gh 的错误输出可能含认证 URL；不得直接回显。
        operation = args[1] if len(args) > 1 else "运行"
        raise PublishError(f"{args[0]} {operation} 失败（退出码 {result.returncode}）；请检查仓库、认证或网络")
    return result


def _canonical_repo(repo: str) -> tuple[str, str]:
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", repo)
    if not match:
        raise PublishError("仓库地址必须是无凭据的 GitHub HTTPS 地址")
    name = f"{match[1]}/{match[2]}"
    return f"https://github.com/{name}", name


def _relative_name(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or any(part in {"", ".", ".."} or part != part.rstrip(" .")
                   or RESERVED_NAME.search(part) for part in name.split("/"))
            or any(part.casefold() == ".git" for part in path.parts)):
        raise PublishError(f"文件路径不安全：{name}")
    return path.as_posix()


def _safe_path(root: Path, name: str) -> Path:
    name = _relative_name(name)
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise PublishError("拒绝将符号链接或目录联接作为同步根目录")
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(name).parts)
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
            raise PublishError(f"拒绝符号链接或目录联接：{name}")
        cursor = cursor.parent
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise PublishError(f"文件路径越界：{name}") from exc
    return candidate


def _sensitive_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return any(SENSITIVE_NAME.search(part) or part.casefold().startswith(".env")
               or part.casefold() in {"id_rsa", "id_ed25519", "id_dsa", ".git-credentials"}
               or Path(part).suffix.casefold() in {".pem", ".key", ".p12", ".pfx"}
               for part in parts)


def _allowed_file(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if any(part.casefold() in EXCLUDED_DIRS for part in parts):
        return False
    if len(parts) == 1:
        return name in ROOT_FILES or (name.startswith(("启动", "检查运行环境")) and name.endswith(".cmd"))
    return parts[0] in CODE_DIRS and Path(name).suffix.casefold() in TEXT_SUFFIXES


def project_profile(source: Path) -> str | None:
    """独立项目声明同时锁定发布仓库和运行包用途；旧源码保持兼容。"""
    path = _safe_path(source, "PROJECT.json")
    if not path.exists():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        profile = config["profile"]
        if profile not in PROJECT_REPOS or config["repository"] != PROJECT_REPOS[profile]:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise PublishError("PROJECT.json 的项目或仓库声明无效") from exc
    return profile


def project_file_allowed(name: str, profile: str | None) -> bool:
    if profile is None:
        return True
    if name.startswith("启动彩灯看板"):
        return profile == "lantern"
    if name.startswith("启动生产看板"):
        return profile == "production"
    if name.startswith("web/"):
        return name.startswith("web/lantern/") if profile == "lantern" else not name.startswith("web/lantern/")
    if profile == "lantern" and name.startswith("docs/"):
        return "lantern" in name.casefold()
    if profile == "lantern" and name.startswith("automation/"):
        return False
    if profile == "production":
        # 生产单解析仍复用 XML/Excel 底层工具，不包含彩灯服务和业务页面。
        if name == "app/lantern_documents.py":
            return True
        return not ("lantern" in name.casefold() or name == "scripts/start_lantern.ps1")
    return True


def collect_files(source: Path, tracked: Iterable[str]) -> dict[str, bytes]:
    """仅读取 tracked 白名单文件；敏感文件名在读取内容之前拦截。"""
    names = [_relative_name(name) for name in tracked]
    sensitive = [name for name in names if _sensitive_name(name)]
    if sensitive:
        raise PublishError("发现禁止发布的敏感文件：" + "、".join(sorted(sensitive)))
    profile = project_profile(source) if "PROJECT.json" in names else None
    files: dict[str, bytes] = {}
    for name in names:
        path = _safe_path(source, name)
        if not _allowed_file(name) or not project_file_allowed(name, profile):
            continue
        if not path.is_file():
            raise PublishError(f"已跟踪文件不存在或不是普通文件：{name}")
        data = path.read_bytes()
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise PublishError(f"白名单文件必须是 UTF-8 文本：{name}") from exc
        if "\x00" in content:
            raise PublishError(f"拒绝二进制文件：{name}")
        if (any(pattern.search(content) for pattern in SECRET_PATTERNS)
                or any(not PLACEHOLDER.search(match[1])
                       for pattern in (HARDCODED_SECRET, ENV_SECRET)
                       for match in pattern.finditer(content))):
            raise PublishError(f"文件疑似包含凭据或私钥：{name}")
        files[name] = data
    return files


def _plan_sync(target: Path, files: dict[str, bytes], previous: Iterable[str]) -> tuple[dict[str, Path], list[str]]:
    previous = set(previous)
    paths = {name: _safe_path(target, name) for name in set(files) | previous}
    for name, path in paths.items():
        if path.exists() and not path.is_file():
            raise PublishError(f"目标文件不是普通文件：{name}")
    changed = sorted(previous - files.keys())
    changed.extend(name for name, data in sorted(files.items())
                   if not paths[name].exists() or paths[name].read_bytes() != data)
    return paths, changed


def sync_files(target: Path, files: dict[str, bytes], previous: Iterable[str]) -> list[str]:
    """预检全部路径后同步；删除操作只作用于目标内的已跟踪普通文件。"""
    previous = set(previous)
    paths, changed = _plan_sync(target, files, previous)
    for name in sorted(previous - files.keys()):
        path = paths[name]
        if path.exists():
            path.unlink()
    for name in changed:
        if name not in files:
            continue
        data = files[name]
        path = paths[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return changed


def require_committed(source: Path) -> None:
    actual = run(["git", "rev-parse", "--show-toplevel"], cwd=source).stdout.strip()
    if Path(actual).resolve() != source.resolve():
        raise PublishError("源目录必须是 Git 仓库根目录")
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source).stdout:
        raise PublishError("源仓库有未提交的 tracked 修改，请先提交本次软件版本")


def _tracked(repo: Path) -> list[str]:
    names = []
    for entry in run(["git", "ls-files", "--stage", "-z"], cwd=repo).stdout.split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        if metadata.split()[0] not in {"100644", "100755"}:
            raise PublishError(f"拒绝已跟踪的符号链接或子模块：{name}")
        names.append(name)
    return names


def require_non_regressing_version(version: str, remote_version: str) -> None:
    """在写入发布快照前阻止旧工作区覆盖较新的远端版本。"""
    def core(value: str) -> tuple[int, int, int]:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?", value.strip())
        if not match:
            raise PublishError("无法核验远端与本机版本，拒绝覆盖发布快照")
        return tuple(int(part) for part in match.groups())
    if core(version) < core(remote_version):
        raise PublishError(f"远端 V{remote_version.strip()} 高于本机 V{version}；须先合并远端功能，拒绝覆盖 main 或创建发布标签")


def publish(source: Path, *, repo: str | None = None, push: bool = False) -> dict[str, object]:
    source = source.resolve()
    require_committed(source)
    profile = project_profile(source)
    expected_repo = PROJECT_REPOS[profile] if profile else DEFAULT_REPO
    repo, repo_name = _canonical_repo(repo or expected_repo)
    if profile and repo != expected_repo:
        raise PublishError("拒绝跨项目发布：仓库必须与 PROJECT.json 一致")
    source_commit = run(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip()
    files = collect_files(source, _tracked(source))
    if "VERSION" not in files:
        raise PublishError("源仓库缺少已提交的 VERSION")
    version = files["VERSION"].decode("utf-8-sig").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?", version):
        raise PublishError("VERSION 必须是语义版本号，例如 0.2.5")
    tag = f"v{version}"
    require_committed(source)
    if run(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip() != source_commit:
        raise PublishError("源仓库提交在准备过程中发生变化，请重新发布")
    try:
        remote = json.loads(run(["gh", "repo", "view", repo_name, "--json", "nameWithOwner,isPrivate"], cwd=source).stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PublishError("无法核验 GitHub 仓库的私有属性") from exc
    if (not isinstance(remote, dict) or not isinstance(remote.get("nameWithOwner"), str)
            or remote["nameWithOwner"].casefold() != repo_name.casefold() or remote.get("isPrivate") is not True):
        raise PublishError("拒绝发布：远端仓库不匹配或不是私有仓库")

    target = _safe_path(source, "runtime/github_publish")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # 只 clone 远端自身；绝不从源仓库克隆或转移其历史。
        run(["git", "-c", "core.autocrlf=false", "clone", "--branch", "main", "--single-branch", repo, str(target)], cwd=source, timeout=180)
    git_dir = target / ".git"
    if (not git_dir.is_dir() or git_dir.is_symlink()
            or getattr(git_dir, "is_junction", lambda: False)()):
        raise PublishError("发布目录必须是独立 Git clone")
    actual = run(["git", "rev-parse", "--show-toplevel"], cwd=target).stdout.strip()
    if Path(actual).resolve() != target.resolve():
        raise PublishError("发布目录不是独立 Git 仓库根目录")
    origin = run(["git", "config", "--get", "remote.origin.url"], cwd=target).stdout.strip()
    if _canonical_repo(origin)[0] != repo:
        raise PublishError("发布目录 origin 与预期仓库不一致")
    if run(["git", "symbolic-ref", "--short", "HEAD"], cwd=target).stdout.strip() != "main":
        raise PublishError("发布目录必须位于 main 分支")
    if run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=target).stdout:
        raise PublishError("发布目录存在未提交或未跟踪文件，请先处理后重试")
    # 固定发布库的换行处理，避免 Windows 全局 autocrlf 在重新 clone 时改变代码字节。
    run(["git", "config", "--local", "core.autocrlf", "false"], cwd=target)
    run(["git", "fetch", "--tags", "origin", "main"], cwd=target, timeout=180)
    remote_version = run(["git", "show", "origin/main:VERSION"], cwd=target).stdout.strip()
    require_non_regressing_version(version, remote_version)
    run(["git", "pull", "--ff-only", "origin", "main"], cwd=target, timeout=180)
    source_history = set(run(["git", "rev-list", source_commit], cwd=source).stdout.splitlines())
    target_history = set(run(["git", "rev-list", "HEAD"], cwd=target).stdout.splitlines())
    if source_history & target_history:
        raise PublishError("发布目录包含源仓库历史，拒绝上传；请使用独立的干净远端历史")
    previous = _tracked(target)
    _, changed = _plan_sync(target, files, previous)
    existing_tag = run(["git", "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"],
                       cwd=target, allowed=(0, 1)).returncode == 0
    if existing_tag and changed:
        raise PublishError(f"版本标签 {tag} 已存在且代码发生变化，请递增 VERSION")
    commit = run(["git", "rev-parse", "HEAD"], cwd=target).stdout.strip()
    if existing_tag:
        tagged_commit = run(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], cwd=target).stdout.strip()
        if tagged_commit != commit:
            raise PublishError(f"版本标签 {tag} 与本次提交不一致，拒绝覆盖标签")
    if changed:
        sync_files(target, files, previous)
        run(["git", "add", "--all", "--", *changed], cwd=target)
        run(["git", "-c", "user.name=Codex", "-c", "user.email=codex@localhost",
             "commit", "-m", f"发布 V{version} 代码快照"], cwd=target)
    commit = run(["git", "rev-parse", "HEAD"], cwd=target).stdout.strip()
    if not existing_tag:
        run(["git", "tag", tag, commit], cwd=target)
    if push:
        # 原子推送避免只更新分支而未更新标签；不强推、不删除任何远端标签。
        run(["git", "push", "--atomic", "origin", "main", f"refs/tags/{tag}"], cwd=target, timeout=180)
    return {"repo": repo, "version": version, "tag": tag, "commit": commit, "files": len(files)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repo", default=None, help="默认使用 PROJECT.json 声明的独立仓库")
    parser.add_argument("--push", action="store_true", help="将已准备的 main 和版本标签原子推送至私有仓库")
    args = parser.parse_args()
    try:
        result = publish(args.workspace, repo=args.repo, push=args.push)
    except (PublishError, OSError) as exc:
        message = str(exc) if isinstance(exc, PublishError) else "文件操作失败，请检查访问权限和磁盘状态"
        print(f"发布失败：{message}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
