"""V0.2.0 可选 PDF 导出，使用隔离的本机 Edge 无头打印。

不读取现有浏览器配置，不启动服务器，不需要第三方 Python 依赖。
调用方只应传入 reports.render_report_html 生成、用户字段已经转义的 HTML。
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import uuid


EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
RENDER_TIMEOUT = 45


class PDFExportError(RuntimeError):
    """提供给本机服务显示的中文 PDF 导出错误。"""


def _stop_process_tree(process: subprocess.Popen) -> None:
    """只终止本次独立渲染的进程树，不匹配或终止其他浏览器。"""
    if process.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def render_report_pdf(html: str, workspace: Path) -> bytes:
    """在 workspace/private/report_render/<uuid> 中打印，返回 PDF bytes。

    每次使用独立 user-data-dir，最多等待 Edge 45 秒；结束后清理本次
    临时文件。清理仅针对经过边界核验的唯一子目录，失败不删除其它文件。
    """
    if not isinstance(html, str) or not html.strip():
        raise PDFExportError("PDF导出失败：打印稿内容为空。")
    if os.name != "nt":
        raise PDFExportError("PDF导出当前仅支持安装了 Microsoft Edge 的 Windows 本机。")
    if not EDGE_PATH.is_file():
        raise PDFExportError("PDF导出不可用：未找到本机 Microsoft Edge，请先使用HTML打印稿。")
    try:
        root = Path(workspace).resolve(strict=True)
        expected_private = root / "private"
        private_root = expected_private.resolve()
        if not private_root.is_relative_to(expected_private):
            raise PDFExportError("PDF导出失败：private目录超出了指定工作区。")
        render_root = (private_root / "report_render").resolve()
        if not render_root.is_relative_to(private_root):
            raise PDFExportError("PDF导出失败：临时目录超出了private目录。")
        render_root.mkdir(parents=True, exist_ok=True)
        render_dir = (render_root / uuid.uuid4().hex).resolve()
        if not render_dir.is_relative_to(render_root) or render_dir == render_root:
            raise PDFExportError("PDF导出失败：临时目录边界核验未通过。")
        render_dir.mkdir()
    except PDFExportError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PDFExportError("PDF导出失败：无法建立工作区内的私有临时目录。") from exc

    process = None
    try:
        input_path = render_dir / "report.html"
        output_path = render_dir / "report.pdf"
        profile_path = render_dir / "edge-profile"
        input_path.write_text(html, encoding="utf-8")
        arguments = [
            str(EDGE_PATH), "--headless=new", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--no-pdf-header-footer",
            "--user-data-dir=" + str(profile_path),
            "--print-to-pdf=" + str(output_path), input_path.as_uri(),
        ]
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            arguments, cwd=render_dir, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            return_code = process.wait(timeout=RENDER_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            _stop_process_tree(process)
            raise PDFExportError("PDF导出超时：本机Edge在45秒内未完成打印，请重试或使用HTML打印稿。") from exc
        if return_code != 0:
            raise PDFExportError("PDF导出失败：本机Edge未正常完成打印，请重试或使用HTML打印稿。")
        if not output_path.is_file():
            raise PDFExportError("PDF导出失败：本机Edge未生成PDF文件，请使用HTML打印稿。")
        pdf = output_path.read_bytes()
        if len(pdf) < 512 or not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-1024:]:
            raise PDFExportError("PDF导出失败：生成文件不完整，请重试或使用HTML打印稿。")
        return pdf
    except PDFExportError:
        raise
    except (OSError, ValueError) as exc:
        raise PDFExportError("PDF导出失败：无法启动本机Edge或读取打印结果，请使用HTML打印稿。") from exc
    finally:
        if process is not None:
            _stop_process_tree(process)
        # 删除前重新解析路径，避免运行期间目录被替换为工作区外的链接。
        try:
            cleanup_dir = render_dir.resolve()
            cleanup_root = render_root.resolve()
            if (cleanup_root == render_root and cleanup_dir.is_relative_to(render_root)
                    and cleanup_dir != render_root):
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        except (OSError, RuntimeError):
            pass
