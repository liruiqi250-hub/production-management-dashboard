"""为接收者 Codex 提供离线安装诊断；仅查询本机健康接口，不连接微信。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import urllib.request

try:
    from scripts.configure_wechat_binding import BindingError, check_binding, reject_links
except ModuleNotFoundError:
    from configure_wechat_binding import BindingError, check_binding, reject_links

ROOT = Path(__file__).resolve().parents[1]

CORE_PROBE = r'''import sys,json,sqlite3,ssl,hashlib,app
hashlib.scrypt(b'probe',salt=b'probe',n=16,r=1,p=1,dklen=16)
print(json.dumps({'version':sys.version.split()[0],'bits':__import__('struct').calcsize('P')*8,'isolated':sys.flags.isolated,'app_version':app.__version__,'sqlite':True,'ssl':True,'scrypt':True}))'''
WECHAT_PROBE = r'''import sys,json,site,importlib,importlib.metadata
modules=['cffi','colorama','comtypes','cryptography','imageio_ffmpeg','numpy','PIL','psutil','pycparser','pyperclip','win32api','pythoncom','sherpa_onnx','soundfile','typing_extensions','uiautomation','winsdk','zstandard','wechatauto','exporter_core']
imports={}
for module in modules:
 try: importlib.import_module(module); imports[module]=True
 except BaseException: imports[module]=False
versions={}
for name in json.loads(sys.argv[1]):
 try: versions[name]=importlib.metadata.version(name)
 except BaseException: versions[name]=None
print(json.dumps({'version':sys.version.split()[0],'bits':__import__('struct').calcsize('P')*8,'isolated':sys.flags.isolated,'ignore_environment':sys.flags.ignore_environment,'user_site_disabled':site.ENABLE_USER_SITE is False,'imports':imports,'versions':versions}))'''


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def component_path(root: Path, relative: str) -> Path:
    path = root / relative
    reject_links(path)
    if not path.resolve(strict=False).is_relative_to(root.resolve()):
        raise BindingError("组件位置超出当前项目目录。")
    return path


def run_probe(executable: Path, args: list[str], root: Path, *, timeout: int = 40) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in
           {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP"}}
    ffmpeg = root / "vendor/wechat/ffmpeg/bin/ffmpeg.exe"
    if ffmpeg.is_file():
        env["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg)
    completed = subprocess.run([str(executable), *args], cwd=root, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0 or len(completed.stdout) > 65536:
        raise ValueError("组件检查未通过")
    # 个别第三方组件有初始化提示，只接受最终 JSON，绝不转发原提示。
    return json.loads(completed.stdout.strip().splitlines()[-1])


def check_core(root: Path, version: str) -> list[dict]:
    results = []
    try:
        python = component_path(root, "vendor/python/python.exe")
        info = run_probe(python, ["-I", "-X", "utf8", "-B", "-c", CORE_PROBE], root)
        ready = info["bits"] == 64 and tuple(map(int, info["version"].split(".")[:2])) >= (3, 12) and info["app_version"] == version and info["isolated"] == 1
        results.append({"id": "core_python", "status": "ready" if ready else "error", "version": info.get("version"),
                        "detail": "包内 Python、应用版本、SQLite、SSL、密码算法已实际检查。" if ready else "包内 Python 隔离性或应用版本不符合要求。"})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        results.append({"id": "core_python", "status": "error", "detail": "包内看板 Python 缺失或无法运行，请重新解压完整运行包。"})
    try:
        browser = component_path(root, "vendor/chromium/chrome-headless-shell.exe")
        result = subprocess.run([str(browser), "--version"], cwd=root, capture_output=True, timeout=15,
                                text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        version_match = re.search(r"\b\d+\.\d+\.\d+\.\d+\b", result.stdout)
        ready = result.returncode == 0 and version_match is not None
        results.append({"id": "pdf_browser", "status": "ready" if ready else "error",
                        "version": version_match.group() if version_match else None,
                        "detail": "包内 PDF 浏览器可以运行。" if ready else "包内 PDF 浏览器检查未通过。"})
    except (OSError, ValueError, subprocess.SubprocessError):
        results.append({"id": "pdf_browser", "status": "error", "detail": "包内 PDF 浏览器缺失或无法运行。"})
    return results


def check_media(root: Path) -> list[dict]:
    """核对本包媒体二进制及随附许可；源码配套附件由发布流程单独验收。"""
    layouts = {"ffmpeg": "vendor/wechat/ffmpeg", "libsndfile": "vendor/wechat/python/Lib/site-packages/_soundfile_data"}
    try:
        lock_path = component_path(root, "dependencies/media-runtime.lock.json")
        lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
        if lock.get("schema") != 1 or lock.get("platform") != "windows-x64" or len(lock["components"]) != len(layouts) or {item["id"] for item in lock["components"]} != set(layouts):
            raise ValueError
        checks = []
        for component in lock["components"]:
            files = component["files"]
            if not isinstance(files, dict) or not files:
                raise ValueError
            matches = True
            for relative, expected in files.items():
                relative_path = PurePosixPath(relative)
                if relative_path.is_absolute() or any(part in ("..", ".") for part in relative_path.parts) or ":" in relative or "\\" in relative or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise ValueError
                target = component_path(root, layouts[component["id"]] + "/" + relative)
                matches = matches and target.is_file() and sha256_file(target) == expected
            checks.append({"id": "media_" + component["id"], "status": "ready" if matches else "error",
                           "detail": "媒体组件二进制、动态库及随附许可的固定校验一致。" if matches else "媒体组件二进制、动态库或许可缺失或被修改。"})
        return checks
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [{"id": "media_integrity", "status": "error", "detail": "媒体组件锁文件、路径或校验资料不符合要求。"}]


def check_wechat(root: Path, *, required: bool) -> list[dict]:
    missing_status = "error" if required else "optional"
    results = []
    try:
        lock = json.loads((root / "dependencies/wechat-collector.lock.json").read_text(encoding="utf-8-sig"))
        python = component_path(root, "vendor/wechat/python/python.exe")
        if not python.is_file():
            return [{"id": "wechat_runtime", "status": missing_status,
                     "detail": "此目录未包含完整微信运行组件；需要采集时请使用 full 完整包。"}]
        wanted = {item["name"]: item["version"] for item in lock["packages"]}
        info = run_probe(python, ["-I", "-X", "utf8", "-B", "-c", WECHAT_PROBE, json.dumps(list(wanted))], root, timeout=90)
        version_ok = info["version"].startswith("3.12.") and info["bits"] == 64
        isolation_ok = info["isolated"] == 1 and info["ignore_environment"] == 1 and info["user_site_disabled"]
        packages_ok = all(info["versions"].get(name) == version for name, version in wanted.items())
        imports_ok = all(info["imports"].values())
        ready = version_ok and isolation_ok and packages_ok and imports_ok
        results.append({"id": "wechat_runtime", "status": "ready" if ready else "error", "version": info["version"],
                        "package_count": len(wanted), "package_versions_verified": packages_ok,
                        "modules_imported": imports_ok, "isolated_from_user_site": bool(isolation_ok),
                        "detail": "包内微信 Python 和固定依赖已实际导入，没有连接微信。" if ready else "微信 Python、依赖版本、DLL 导入或用户目录隔离检查未通过。"})
        source = component_path(root, "vendor/wechat/source/wechat-chat-export-1.3.4")
        source_ok = all(component_path(root, "vendor/wechat/source/wechat-chat-export-1.3.4/" + name).is_file() and sha256_file(source / name) == expected
                        for name, expected in lock["exporter"]["files"].items())
        results.append({"id": "wechat_exporter", "status": "ready" if source_ok else "error", "detail": "微信采集源码与固定版本校验一致。" if source_ok else "微信采集源码缺失或校验不一致。"})
        rust = component_path(root, "vendor/wechat/source/wechat-chat-export-1.3.4/tools/rust-silk.exe")
        rust_ok = rust.is_file() and sha256_file(rust) == lock["rust_silk"]["file_sha256"]
        results.append({"id": "silk_decoder", "status": "ready" if rust_ok else "error", "detail": "SILK 语音解码器校验一致。" if rust_ok else "SILK 语音解码器缺失或被修改。"})
        model_root = component_path(root, "vendor/wechat/source/wechat-chat-export-1.3.4/tools/asr/sensevoice")
        wanted_models = {**lock["sensevoice"]["files"], "MODEL_LICENSE.txt": lock["sensevoice"]["license_sha256"]}
        model_ok = all(component_path(root, "vendor/wechat/source/wechat-chat-export-1.3.4/tools/asr/sensevoice/" + name).is_file() and sha256_file(model_root / name) == expected for name, expected in wanted_models.items())
        source_record = component_path(root, "vendor/wechat/source/wechat-chat-export-1.3.4/tools/asr/sensevoice/MODEL_SOURCE.json")
        model_source_ok = False
        if source_record.is_file() and source_record.stat().st_size <= 65536:
            model_source = json.loads(source_record.read_text(encoding="utf-8"))
            model_source_ok = isinstance(model_source, dict) and all(model_source.get(key) == lock["sensevoice"][key] for key in ("model_name", "url", "source_repository"))
        model_ok = model_ok and model_source_ok
        results.append({"id": "sensevoice_model", "status": "ready" if model_ok else "error", "detail": "离线语音模型、词表、出处及完整模型许可校验一致；此检查未运行语音识别。" if model_ok else "离线语音模型、词表、出处或模型许可缺失或校验失败。"})
        media_checks = check_media(root)
        results.extend(media_checks)
        if any(item["status"] == "error" for item in media_checks):
            return results
        ffmpeg = component_path(root, "vendor/wechat/ffmpeg/bin/ffmpeg.exe")
        result = subprocess.run([str(ffmpeg), "-version"], cwd=root, capture_output=True, timeout=15,
                                text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ffmpeg_ok = result.returncode == 0 and "ffmpeg version" in result.stdout
        results.append({"id": "ffmpeg", "status": "ready" if ffmpeg_ok else "error", "detail": "包内 FFmpeg 及动态库可以运行。" if ffmpeg_ok else "包内 FFmpeg 或动态库无法运行。"})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        results.append({"id": "wechat_components", "status": "error", "detail": "微信完整组件检查失败，请核对 full 包完整性；没有连接微信。"})
    return results


def check_service(root: Path, version: str, port: int) -> dict:
    workspace_id = hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()
    suffix = "V0.2.0" if port == 8769 else f"port_{port}"
    pid_path = component_path(root, f"private/server_{suffix}.pid")
    try:
        saved_pid = int(pid_path.read_text(encoding="utf-8-sig").strip()) if pid_path.is_file() else None
        if saved_pid is not None and saved_pid <= 0:
            raise ValueError
    except (OSError, ValueError):
        return {"id": "dashboard_service", "status": "error", "code": "pid_record_invalid", "detail": "服务 PID 记录无效，未停止任何进程。"}
    try:
        # 禁用系统代理，诊断只允许访问明确的本机回环地址。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            health = json.loads(response.read(8192))
    except (OSError, ValueError):
        return {"id": "dashboard_service", "status": "needs_configuration", "code": "service_not_ready",
                "recorded_pid": saved_pid, "detail": "本机服务尚未就绪，请使用当前版本启动入口；未停止任何进程。"}
    healthy = isinstance(health, dict) and health.get("status") == "ready" and health.get("workspace_id") == workspace_id and health.get("version") == version
    process_match = False
    listener_match = False
    if os.name == "nt" and saved_pid:
        code = f"""$ErrorActionPreference='Stop'; $p=Get-CimInstance Win32_Process -Filter 'ProcessId = {saved_pid}'; $c=if($p){{$p.CommandLine}}else{{''}}; $ids=@(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); @{{process_match=[bool]($c -match '(?:^|\\s)-m\\s+app\\.server(?:\\s|$)' -and $c -match '(?:^|\\s)--port\\s+{port}(?:\\s|$)' -and $c -match '(?:^|\\s)--workspace-id\\s+{workspace_id}(?:\\s|$)'); listener_match=[bool]($ids.Count -eq 1 -and $ids[0] -eq {saved_pid})}} | ConvertTo-Json -Compress"""
        try:
            info = run_probe(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", code], root, timeout=25)
            process_match, listener_match = info["process_match"], info["listener_match"]
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass
    ready = healthy and process_match and listener_match
    return {"id": "dashboard_service", "status": "ready" if ready else "error", "code": "service_verified" if ready else "service_identity_mismatch",
            "recorded_pid": saved_pid, "workspace_id_matches": bool(healthy), "pid_command_matches": bool(process_match),
            "listener_pid_matches": bool(listener_match), "detail": "本机服务版本、工作区及监听 PID 一致。" if ready else "无法确认端口服务属于当前项目及当前版本；未停止任何进程。"}


def diagnose(root: Path = ROOT, *, require_wechat: bool = False, port: int = 8769) -> dict:
    checks = []
    version = "unknown"
    try:
        reject_links(root)
        version_text = (root / "VERSION").read_text(encoding="utf-8-sig").strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", version_text):
            raise ValueError
        version = version_text
        platform_ok = os.name == "nt" and struct.calcsize("P") == 8
        checks.append({"id": "platform", "status": "ready" if platform_ok else "error", "detail": "Windows x64 环境符合要求。" if platform_ok else "需要 Windows x64 运行环境。"})
        checks.extend(check_core(root, version))
        checks.extend(check_wechat(root, required=require_wechat))
        binding = check_binding(root)
        if not require_wechat and binding["status"] == "needs_configuration":
            binding["status"] = "optional"
        checks.append({"id": "wechat_binding", **binding})
        checks.append(check_service(root, version, port))
    except (OSError, ValueError, TypeError):
        checks.append({"id": "installation_root", "status": "error", "detail": "项目目录、版本文件或组件路径不符合要求。"})
    status = "error" if any(check["status"] == "error" for check in checks) else (
        "needs_configuration" if any(check["status"] == "needs_configuration" for check in checks) else "ready")
    return {"schema_version": 1, "software_version": version, "status": status,
            "exit_code": {"ready": 0, "needs_configuration": 2, "error": 1}[status],
            "required_wechat": require_wechat, "checks": checks, "wechat_connected": False,
            "messages_read": False, "scheduled_task_installed": False,
            "acceptance_note": "环境、账号授权、真实采集、定时更新分别验收；本诊断未读取微信消息或安装任务。"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出可机器读取且不含本机账号和目录值的 JSON")
    parser.add_argument("--require-wechat", action="store_true", help="完整采集电脑必须具备微信 Python、模型、解码器及本机绑定")
    parser.add_argument("--port", type=int, default=8769, help="本机看板健康检查端口，默认 8769")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 至 65535 之间")
    result = diagnose(require_wechat=args.require_wechat, port=args.port)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"生产管理看板 V{result['software_version']} 安装诊断：{result['status']}")
        for check in result["checks"]:
            print(f"[{check['status']}] {check['detail']}")
        print(result["acceptance_note"])
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
