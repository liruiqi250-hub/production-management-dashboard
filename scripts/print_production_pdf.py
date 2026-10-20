"""用 LibreOffice 自带 Python 执行受控 PDF 打印；--dry-run 只加载、数页，绝不打印。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

PRINTER_NAME = "KONICA MINOLTA bizhub C226 PCL (192.168.10.199) UPD"
MAX_PDF_BYTES = 100 * 1024 * 1024


def inside(root, relative):
    root = Path(root)
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("打印根目录不能使用链接。")
    root = root.resolve()
    parts = relative.split("/")
    if any(not p or p in {".", ".."} or ":" in p or "\\" in p for p in parts):
        raise ValueError("打印路径无效。")
    target = root.joinpath(*parts)
    cursor = target
    while cursor != root:
        if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
            raise ValueError("打印路径不能使用链接。")
        cursor = cursor.parent
    if not target.resolve().is_relative_to(root):
        raise ValueError("打印文件超出范围。")
    return target


def validate_manifest(root, manifest_path):
    root = Path(root).resolve()
    path = Path(manifest_path)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("打印清单不能使用链接。")
    try:
        relative = path.absolute().relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("打印清单超出范围。") from exc
    match = re.fullmatch(r"private/production_print_jobs/([a-f0-9]{32})/manifest\.json", relative)
    if not match or inside(root, relative).resolve() != path.resolve() or not path.is_file() or path.stat().st_size > 32768:
        raise ValueError("打印清单位置无效。")
    data = json.loads(path.read_text("utf-8-sig"))
    expected = {"schema", "job_id", "output_id", "payload", "payload_sha256", "printer_name", "pages", "copies", "document_pages"}
    if (not isinstance(data, dict) or set(data) != expected or data.get("schema") != 1
            or data.get("job_id") != match[1] or data.get("payload") != "payload.pdf"
            or data.get("printer_name") != PRINTER_NAME
            or not isinstance(data.get("output_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", data["output_id"])
            or not isinstance(data.get("payload_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", data["payload_sha256"])):
        raise ValueError("打印清单字段无效。")
    pages, count, copies = data.get("pages"), data.get("document_pages"), data.get("copies")
    if (type(count) is not int or not 1 <= count <= 10000 or type(copies) is not int or not 1 <= copies <= 5
            or not isinstance(pages, list) or not pages or any(type(p) is not int or not 1 <= p <= count for p in pages)
            or sorted(set(pages)) != pages or len(pages) * copies > 250):
        raise ValueError("打印页码、份数或总量无效。")
    payload = inside(root, "private/production_print_jobs/" + match[1] + "/payload.pdf")
    if not payload.is_file() or not 0 < payload.stat().st_size <= MAX_PDF_BYTES:
        raise ValueError("打印 PDF 缺失或大小无效。")
    with payload.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("打印副本不是 PDF。")
        handle.seek(0)
        if hashlib.file_digest(handle, "sha256").hexdigest() != data["payload_sha256"]:
            raise ValueError("打印 PDF 摘要不符。")
    return data, payload


def validate_config(root):
    path = inside(root, "private/production_printing.json")
    if not path.is_file():
        raise ValueError("办公室打印尚未配置。")
    data = json.loads(path.read_text("utf-8-sig"))
    if (not isinstance(data, dict) or set(data) != {"schema", "enabled", "printer_name"}
            or data.get("schema") != 1 or data.get("enabled") is not True or data.get("printer_name") != PRINTER_NAME):
        raise ValueError("办公室打印尚未启用或目标无效。")


def run(root, manifest_path, *, pipe_name, dry_run=False):
    data, payload = validate_manifest(root, manifest_path)
    if not isinstance(pipe_name, str) or not re.fullmatch(r"production_print_[a-f0-9]{32}", pipe_name):
        raise ValueError("打印组件管道无效。")
    if not dry_run:
        validate_config(root)
    import uno
    from com.sun.star.beans import PropertyValue

    def prop(name, value):
        item = PropertyValue()
        item.Name, item.Value = name, value
        return item

    desktop, document, attempted = None, None, False
    stage = "连接 UNO"
    try:
        context = uno.getComponentContext()
        resolver = context.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", context)
        deadline, remote = time.monotonic() + 25, None
        while time.monotonic() < deadline:
            try:
                remote = resolver.resolve("uno:pipe,name=" + pipe_name + ";urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.1)
        if remote is None:
            raise ValueError("本机打印组件连接超时。")
        desktop = remote.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", remote)
        stage = "载入 PDF"
        options = (prop("Hidden", True), prop("ReadOnly", True), prop("Silent", True),
                   prop("FilterName", "draw_pdf_import"),
                   prop("MacroExecutionMode", uno.getConstantByName("com.sun.star.document.MacroExecMode.NEVER_EXECUTE")),
                   prop("UpdateDocMode", uno.getConstantByName("com.sun.star.document.UpdateDocMode.NO_UPDATE")))
        document = desktop.loadComponentFromURL(payload.resolve().as_uri(), "_blank", 0, options)
        if document is None or not hasattr(document, "getDrawPages"):
            raise ValueError("PDF 未能以只读绘图文档载入。")
        count = document.getDrawPages().getCount()
        stage = "核对页数"
        if count != data["document_pages"]:
            raise ValueError("PDF 实际页数与提交时不同，本次不打印。")
        if dry_run:
            return {"state": "dry_run", "document_pages": count, "pages": data["pages"],
                    "copies": data["copies"], "message": "仅完成 PDF 载入和页数核验，未调用打印。"}
        validate_config(root)
        document.setPrinter((prop("Name", PRINTER_NAME),))
        attempted = True
        document.print((prop("Pages", ";".join(str(p) for p in data["pages"])), prop("CopyCount", data["copies"]),
                        prop("Collate", True), prop("Wait", True)))
        return {"state": "submitted", "message": "已交给 Windows 打印队列，不代表已经出纸。"}
    except Exception as exc:
        result = {"state": "unknown" if attempted else "failed",
                  "message": "提交结果未知，请先检查打印队列，勿重复打印。" if attempted else "本机 PDF 打印校验或载入失败，未提交。"}
        if dry_run:
            result.update(stage=stage, error_type=type(exc).__name__, diagnostic=str(exc)[:300])
        return result
    finally:
        if document is not None:
            try:
                document.close(True)
            except Exception:
                try:
                    document.dispose()
                except Exception:
                    pass
        if desktop is not None:
            try:
                desktop.terminate()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pipe", required=True)
    args = parser.parse_args()
    try:
        result = run(args.root, args.manifest, pipe_name=args.pipe, dry_run=args.dry_run)
    except Exception:
        result = {"state": "failed", "message": "打印组件输入或运行环境无效，未提交。"}
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("state") in {"submitted", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
