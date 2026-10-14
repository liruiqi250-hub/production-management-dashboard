"""本机附件读取。PDF文字优先，扫描PDF和图片使用Windows离线OCR。"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from app.lantern_documents import read_rows, extract_pi, extract_packing

SUPPORTED = {'.xls', '.xlsx', '.csv', '.txt', '.docx', '.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}
MAX_TEXT = 120000


def ocr_rows(path, pages=None):
    if os.name != 'nt':
        raise ValueError('图片和扫描PDF需要Windows文字识别组件')
    script = Path(__file__).resolve().parents[1] / 'scripts/ocr_lantern_document.ps1'
    with tempfile.TemporaryDirectory(dir=path.parent, prefix='ocr_') as folder:
        output = Path(folder) / 'text.json'
        args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned',
                '-File', str(script), '-InputPath', str(path), '-OutputPath', str(output)]
        if pages is not None:
            args += ['-PageIndices', ','.join(str(page) for page in pages)]
        try:
            result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=300,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('本机OCR未完成，请检查Windows文字识别语言组件或核对原件')
            value = json.loads(output.read_text('utf-8-sig'))
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise ValueError('本机OCR未完成，原附件保留待人工核对') from exc
    rows = []
    for page in value['pages']:
        for line in page['lines']:
            cells, previous, cell = [], None, ''
            for word in line['words']:
                gap = word['x'] - (previous['x'] + previous['width']) if previous else 0
                if previous and gap > max(12, previous['height'] * 1.2):
                    cells.append(cell); cell = ''
                cell += (' ' if cell and word['text'].isascii() and previous and previous['text'].isascii() else '') + word['text']
                previous = word
            if cell:
                cells.append(cell)
            rows.append(cells or [line['text']])
    return rows, ['使用本机OCR（' + str(value.get('language', '待核对')) + '），文字、数字和表格列可能误识别，请对照原件核对。']


def document_rows(filename, content, scratch):
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED:
        raise ValueError('暂不自动读取此格式；旧DOC请转为DOCX/PDF，压缩包及附加邮件需人工处理')
    if suffix not in {'.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}:
        return read_rows(filename, content), []
    path = Path(scratch) / ('read_source' + suffix)
    path.write_bytes(content)
    if suffix != '.pdf':
        return ocr_rows(path)
    from pypdf import PdfReader
    import io
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise ValueError('PDF已加密，请提供可读取的原件后重试')
    if len(reader.pages) > 100:
        raise ValueError('PDF超过100页，请拆分后核对')
    page_rows, scans = {}, []
    for index, page in enumerate(reader.pages):
        text = page.extract_text(extraction_mode='layout') or ''
        if len(text.strip()) < 20:
            scans.append(index)
        else:
            page_rows[index] = [re.split(r'\s{2,}|\t', line.strip()) for line in text.splitlines() if line.strip()]
    notes = []
    if scans:
        # OCR保留扫描页顺序；文字页不重复OCR。
        for index in scans:
            rows, limits = ocr_rows(path, [index]); page_rows[index] = rows; notes.extend(limits)
    return [row for index in sorted(page_rows) for row in page_rows[index]], list(dict.fromkeys(notes))


def production_fields(rows):
    labels = {'name': r'项目名称|订单名称|project(?:\s*name)?', 'order_no': r'生产单号|订单编号|订单号|order\s*(?:no\.?|number)',
              'country': r'国家|目的国|country', 'voltage': r'电压|voltage',
              'completion_date': r'完工日期|完成日期|交货日期|交期|delivery\s*date',
              'salesperson': r'业务员|销售员|salesperson', 'merchandiser': r'跟单员|跟单|merchandiser'}
    result = {}
    for key, pattern in labels.items():
        for row in rows:
            for i, cell in enumerate(row):
                match = re.fullmatch(r'\s*(?:' + pattern + r')\s*[:：]?\s*(.*?)\s*', cell, re.I)
                if match:
                    value = match[1] or next((value for value in row[i+1:] if value.strip()), '')
                    if value:
                        result[key] = value.strip(); break
            if key in result:
                break
    if result.get('completion_date'):
        value = re.sub(r'[年月./]', '-', result['completion_date']).replace('日', '')
        try:
            from datetime import date
            parts = value.split('-'); result['completion_date'] = date(*map(int, parts)).isoformat()
        except (ValueError, TypeError):
            result['completion_date'] = ''
    products = []
    for index, row in enumerate(rows):
        names = [i for i, value in enumerate(row) if re.fullmatch(r'产品名称|品名|product\s*(?:name)?|description', value.strip(), re.I)]
        if not names:
            continue
        name_col = names[0]
        spec_col = next((i for i, value in enumerate(row) if re.search(r'规格|尺寸|specification|size', value, re.I)), None)
        qty_col = next((i for i, value in enumerate(row) if re.search(r'数量|quantity|qty', value, re.I)), None)
        for values in rows[index+1:]:
            name = values[name_col].strip() if name_col < len(values) else ''
            if not name or re.fullmatch(r'总计|合计|total', name, re.I):
                continue
            quantity = values[qty_col].strip() if qty_col is not None and qty_col < len(values) else ''
            if not re.fullmatch(r'\d+(?:\.\d+)?', quantity):
                continue
            products.append({'name': name[:300], 'specification': values[spec_col][:1000] if spec_col is not None and spec_col < len(values) else '', 'quantity': quantity})
            if len(products) >= 100:
                break
        break
    result['products'] = products
    return result


def extract_document(filename, content, scratch):
    rows, notes = document_rows(filename, content, scratch)
    text = '\n'.join(' | '.join(row) for row in rows)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]; notes.append('附件文字超过120000字符，预览及自动字段只覆盖前段，请核对完整原件。')
        rows = [re.split(r' \| ', line) for line in text.splitlines()]
    candidates = {'production': {'fields': production_fields(rows), 'warnings': []}}
    for kind, extractor in [('pi', extract_pi), ('packing', extract_packing)]:
        try:
            candidates[kind] = extractor(rows)
        except (ValueError, IndexError, KeyError, TypeError):
            candidates[kind] = {'fields': {}, 'warnings': ['未能完整提取此类资料，请核对类型和原件后填写。']}
    patterns = {'production': r'生产单|production\s*order|work\s*order',
                'pi': r'(?<![a-z])pi(?![a-z])|proforma|形式发票|invoice(?!\s*(?:no|number|#))',
                'packing': r'装箱|packing\s*list|packlist'}
    kinds = [kind for kind, pattern in patterns.items() if re.search(pattern, filename, re.I)]
    if not kinds:
        kinds = [kind for kind, pattern in patterns.items() if re.search(pattern, text[:2000], re.I)]
    if not kinds and candidates['packing']['fields'].get('packages'):
        kinds = ['packing']
    kind = kinds[0] if len(kinds) == 1 else 'unknown'
    if kind == 'unknown':
        notes.append('附件类型不明确，请选择生产单、PI或装箱单再核对；不按文件名猜写台账。')
    if not text.strip():
        notes.append('未读取到文字，请核对原件后手工填写。')
    return {'kind': kind, 'fields': candidates.get(kind, {}).get('fields', {}), 'candidates': candidates,
            'text': text, 'warnings': notes + candidates.get(kind, {}).get('warnings', [])}
