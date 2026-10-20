"""同源浏览器打印页：使用已生成PDF的实际页面，不嵌入PDF插件。"""
from html import escape

from app import __version__
from app.production_printing import parse_pages


def render_print_page(metadata, pdf_path, page_spec=""):
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    selected = parse_pages(page_spec, len(reader.pages))
    if len(selected) > 80:
        raise ValueError("浏览器打印每次最多准备80页，请填写页码范围分批打印。")
    title = str(metadata.get("filename") or "生产资料")
    output_id = metadata["id"]
    sheets = []
    for page_number in selected:
        page = reader.pages[page_number - 1]
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if int(page.get("/Rotate", 0)) % 180:
            width, height = height, width
        orientation = "landscape" if width > height else "portrait"
        source = f"/api/production/document-files/{output_id}/pages/{page_number}.png?quality=print"
        sheets.append(f'<section class="print-sheet {orientation}" aria-label="原文件第{page_number}页"><img data-print-src="{escape(source, quote=True)}" alt="原文件第{page_number}页"></section>')
    label = ",".join(str(page) for page in selected)
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)} · 打印</title><link rel="stylesheet" href="/document-print.css?v={__version__}">'
            f'<script src="/document-print.js?v={__version__}" defer></script></head><body>'
            f'<header class="print-toolbar"><div><h1>打印生产资料</h1><p class="print-filename">{escape(title)}</p>'
            f'<p>本次准备 {len(selected)} 页 · 原文件页码 {escape(label)}</p></div>'
            '<div class="print-actions"><button id="print-document" type="button" disabled>正在准备页面…</button><button id="retry-pages" type="button" hidden>重新加载</button><a href="/">返回看板</a></div>'
            '<p id="print-status" role="status" aria-live="polite">正在读取实际打印页面，请稍候。</p>'
            '<p class="print-hint">页面就绪后点击“打印 / 云打印”，在系统对话框中选择打印机和份数。使用云打印前，请先在当前电脑配置对应云打印机；此处不自动提交到云端。</p>'
            '</header><main class="print-pages">' + "".join(sheets) + '</main></body></html>').encode("utf-8")
