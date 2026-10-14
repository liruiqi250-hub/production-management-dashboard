"""PI、装箱总计提取及原模板XML最小填写；业务原件仅存本机。"""
from __future__ import annotations
import csv
import copy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
from xml.etree import ElementTree as ET
import zipfile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
COMPANIES = {'dayang': '大洋', 'yunyi': '云艺', 'jinnai': '金耐'}
FIELDS = {'buyer', 'buyer_address', 'invoice_no', 'contract_no', 'trade_terms', 'currency', 'amount',
          'quantity', 'quantity_unit', 'product_description', 'hs_code', 'invoice_date', 'payment_terms',
          'packing_type', 'packages', 'gross_weight', 'net_weight', 'volume', 'weight_unit', 'volume_unit'}
TOTAL = re.compile(r'^(?:总计|合计|总合计|total(?:\s*(?:amount|weight|sum))?)[\s:：]*$', re.I)


def decimal_text(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('缺少数值')
    value = str(value).strip()
    if ',' in value and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?', value):
        raise ValueError('逗号数值含义不明确，请使用小数点或规范千位分隔后核对')
    value = value.replace(',', '')
    if not re.fullmatch(r'\d+(?:\.\d+)?', value):
        raise ValueError('数值需为非负数字，不含公式或单位')
    number = Decimal(value)
    if not number.is_finite() or number > Decimal('1000000000000'):
        raise ValueError('数值超出可处理范围')
    return format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f')


def safe_zip(data):
    package = zipfile.ZipFile(io.BytesIO(data))
    names = package.namelist()
    if len(names) != len(set(names)) or len(names) > 2000 or sum(x.file_size for x in package.infolist()) > 300 * 1024 * 1024:
        package.close()
        raise ValueError('Office文件过大或包含重复部件')
    for name in names:
        if name.startswith('/') or '\\' in name or '..' in name.split('/'):
            package.close()
            raise ValueError('Office文件部件路径无效')
    return package


def workbook_parts(package):
    workbook = ET.fromstring(package.read('xl/workbook.xml'))
    rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(package.read('xl/_rels/workbook.xml.rels'))}
    result = {}
    for sheet in workbook.findall('s:sheets/s:sheet', NS):
        target = rels[sheet.attrib['{' + NS['r'] + '}id']]
        path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        if not path.startswith('xl/worksheets/') or not path.endswith('.xml'):
            raise ValueError('工作表部件不受支持')
        result[sheet.attrib['name']] = path
    return result


def column_index(address):
    number = 0
    for char in re.match('[A-Z]+', address).group():
        number = number * 26 + ord(char) - 64
    return number - 1


def read_rows(filename, data):
    suffix = Path(filename).suffix.casefold()
    if suffix == '.xls':
        try:
            import xlrd
        except ImportError as exc:
            raise ValueError('旧Excel读取组件未就绪，请使用完整运行包') from exc
        book = xlrd.open_workbook(file_contents=data, on_demand=True)
        rows = []
        for sheet in book.sheets():
            if sheet.nrows > 10000 or sheet.ncols > 100:
                raise ValueError('表格超出10000行/100列读取范围')
            rows += [[str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)] for r in range(sheet.nrows)]
        book.release_resources()
        return rows
    if suffix == '.xlsx':
        with safe_zip(data) as package:
            strings = []
            if 'xl/sharedStrings.xml' in package.namelist():
                strings = [''.join(n.itertext()) for n in ET.fromstring(package.read('xl/sharedStrings.xml'))]
            rows = []
            for part in workbook_parts(package).values():
                sheet = ET.fromstring(package.read(part))
                for row in sheet.findall('s:sheetData/s:row', NS):
                    result = []
                    for cell in row.findall('s:c', NS):
                        index = column_index(cell.attrib['r'])
                        if index >= 100 or int(row.attrib['r']) > 10000:
                            raise ValueError('表格超出读取范围')
                        result += [''] * max(0, index + 1 - len(result))
                        if cell.get('t') == 'inlineStr':
                            value = ''.join(x.text or '' for x in cell.findall('.//s:t', NS))
                        else:
                            value = cell.findtext('s:v', '', NS)
                            if cell.get('t') == 's':
                                value = strings[int(value)]
                        result[index] = value
                    rows.append(result)
            return rows
    if suffix == '.docx':
        with safe_zip(data) as package:
            doc = ET.fromstring(package.read('word/document.xml'))
        rows = []
        body = doc.find('w:body', NS)
        for element in body:
            if element.tag.endswith('}p'):
                rows.append([''.join(x.text or '' for x in element.findall('.//w:t', NS))])
            elif element.tag.endswith('}tbl'):
                for row in element.findall('w:tr', NS):
                    values = []
                    for cell in row.findall('w:tc', NS):
                        value = '\n'.join(''.join(x.text or '' for x in p.findall('.//w:t', NS)) for p in cell.findall('w:p', NS)).strip()
                        span = cell.find('w:tcPr/w:gridSpan', NS)
                        count = int(span.get('{'+NS['w']+'}val')) if span is not None else 1
                        if not 1 <= count <= 100:
                            raise ValueError('Word表格合并列超出范围')
                        values.extend([value] * count)
                    rows.append(values)
        return rows
    if suffix in {'.csv', '.txt'}:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('gb18030')
        return list(csv.reader(io.StringIO(text))) if suffix == '.csv' else [[x] for x in text.splitlines()]
    raise ValueError('支持docx、xls、xlsx、CSV或TXT；扫描件请先由Codex核对后输入')


def extract_packing(rows):
    columns, header = {}, -1
    patterns = {'packages': r'件数|箱数|packing\s*pcs|packages|cartons', 'gross_weight': r'毛重|gross',
                'net_weight': r'净重|net\.?\s*w', 'volume': r'体积|volume|^vol(?:\s|$)|cbm'}
    for index, row in enumerate(rows):
        matches = {key: next((c for c, value in enumerate(row) if re.search(pattern, value, re.I)), None)
                   for key, pattern in patterns.items()}
        if all(c is not None for c in matches.values()):
            columns, header = matches, index
            break
    if header < 0:
        raise ValueError('未找到件数、毛重、净重、体积四列，请保留明确表头或直接输入合计')
    totals = [(index, row) for index, row in enumerate(rows[header + 1:], header + 1) if any(TOTAL.fullmatch(x.strip()) for x in row if x)]
    preferred = [x for x in totals if any(re.match(r'^总计[\s:：]*$', v) for v in x[1])]
    candidates = preferred or totals
    warnings, fields = [], {}
    if len(candidates) > 1:
        raise ValueError('存在多个合计行，请将最终总数标为唯一“总计”，避免混入分组合计')
    selected = candidates[0] if candidates else None
    detail = []
    for index, row in enumerate(rows[header + 1:], header + 1):
        if selected and index >= selected[0]:
            break
        if any(TOTAL.fullmatch(x.strip()) for x in row if x):
            continue
        try:
            detail.append({key: Decimal(decimal_text(row[col])) for key, col in columns.items()})
        except (ValueError, IndexError):
            continue
    for key, col in columns.items():
        if selected:
            try:
                fields[key] = decimal_text(selected[1][col])
            except (ValueError, IndexError) as exc:
                raise ValueError('合计行四项数值不完整，请核对后填写') from exc
            if detail and abs(sum(d[key] for d in detail) - Decimal(fields[key])) > Decimal('0.001'):
                warnings.append(f'{patterns[key].split("|")[0]}：合计与可读取明细不一致，请人工核对')
        elif detail:
            fields[key] = decimal_text(sum(d[key] for d in detail))
        else:
            raise ValueError('没有可读取的总计或完整装箱明细')
    if Decimal(fields['packages']) != Decimal(fields['packages']).to_integral_value():
        warnings.append('总件数不是整数，请核对包装件数和产品数量是否混淆')
    if Decimal(fields['net_weight']) > Decimal(fields['gross_weight']):
        warnings.append('净重大于毛重，请核对原表或计量单位')
    heading = ' '.join(rows[header])
    fields['weight_unit'] = 'kg' if re.search(r'公斤|\bkg(?:s)?\b', heading, re.I) else ''
    fields['volume_unit'] = 'm3' if re.search(r'立方米|cbm|m[³3]', heading, re.I) else ''
    if not fields['weight_unit'] or not fields['volume_unit']:
        warnings.append('原表未明确重量或体积单位，请在导出前确认公斤和立方米')
    warnings.insert(0, f'已取第{selected[0] + 1}行总计/合计，未重复叠加明细' if selected else f'未找到总计，按{len(detail)}行完整明细求和，需核对')
    return {'fields': fields, 'warnings': warnings}


def extract_pi(rows):
    cells = []
    for row in rows:
        for value in row:
            value = str(value).strip()
            if value and (not cells or value != cells[-1]):
                cells.append(value)
    lines = [line.strip() for cell in cells for line in cell.splitlines() if line.strip()]
    text = '\n'.join(lines)
    fields, warnings = {}, []
    patterns = {'invoice_no': r'INV(?:OICE)?\s*NO[.,]?\s*[:：]\s*([^\s]+)',
                'contract_no': r'CONTRACT\s*NO[.,]?\s*[:：]\s*([^\s]+)',
                'trade_terms': r'(?:Trade\s*term|Price\s*term|贸易条款|成交方式)\s*[:：]\s*([^\n]+)',
                'payment_terms': r'(?:Terms?\s*of\s*Payment|付款方式)\s*[:：]\s*([^\n]+)',
                'packing_type': r'(?:Packaging|包装方式)\s*[:：]\s*([^\n]+)'}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            fields[key] = match[1].strip()
    for index, line in enumerate(lines):
        match = re.match(r'^(?:Buyer|Consignee|收货人|买方)\s*[:：]\s*(.*)$', line, re.I)
        if match:
            following = [match[1]] if match[1] else []
            for later in lines[index + 1:]:
                if re.match(r'^(?:INV|CONTRACT|CONTACT|DATE|TRADE|PAYMENT|SHIP|BANK|TOTAL|COMMODITY|贸易|金额|品名)', later, re.I):
                    break
                following.append(later)
            if following:
                fields['buyer'] = following[0]
                fields['buyer_address'] = '\n'.join(following[1:])
            break
    header = next((i for i, row in enumerate(rows) if any(re.search(r'COMMODITY|商品名称|品名', value, re.I) for value in row)
                   and any(re.search(r'AMOUNT|金额', value, re.I) for value in row)), None)
    items = []
    if header is not None:
        names = rows[header]
        index = {key: next((c for c, value in enumerate(names) if re.search(pattern, value, re.I)), None)
                 for key, pattern in {'product_description': r'COMMODITY|商品名称|品名', 'hs_code': r'HS\s*CODE|商品编码',
                                      'quantity': r'QUANTITY|数量', 'amount': r'AMOUNT|金额'}.items()}
        for row in rows[header + 1:]:
            if any(re.search(r'^TOTAL|合计|总计|Packaging|Trade\s*term', v.strip(), re.I) for v in row):
                break
            try:
                item = {key: row[col].strip() for key, col in index.items() if col is not None and col < len(row)}
                item['amount'] = decimal_text(re.sub(r'^(?:USD|EUR|CNY|RMB|GBP|[$€£¥])\s*', '', item['amount'], flags=re.I))
                item['quantity'] = decimal_text(item['quantity'])
                if item.get('product_description'):
                    items.append(item)
            except (KeyError, ValueError):
                continue
    if items:
        fields.update({'product_description': '; '.join(x['product_description'] for x in items),
                       'quantity': decimal_text(sum(Decimal(x['quantity']) for x in items)),
                       'amount': decimal_text(sum(Decimal(x['amount']) for x in items))})
        codes = {x.get('hs_code', '') for x in items} - {''}
        if len(codes) == 1:
            fields['hs_code'] = codes.pop()
        if len(items) > 1:
            warnings.append('PI含多项商品，当前汇总品名和数量，生成前请核对能否合并为同一申报行')
    currencies = {x.upper() for x in re.findall(r'\b(?:USD|EUR|GBP|CNY|RMB|CAD|AUD)\b', text, re.I)}
    explicit_totals = set()
    for row in rows:
        for index, cell in enumerate(row):
            match = re.fullmatch(r'\s*(?:TOTAL\s*AMOUNT|总金额|金额合计)\s*[:：]?\s*(.*?)\s*', cell, re.I)
            if not match:
                continue
            for value in ([match[1]] + row[index+1:]):
                candidate = re.sub(r'^(?:USD|EUR|CNY|RMB|GBP|[$€£¥])\s*', '', value.strip(), flags=re.I)
                try:
                    explicit_totals.add(decimal_text(candidate))
                except ValueError:
                    continue
    if len(explicit_totals) == 1:
        total = next(iter(explicit_totals))
        if fields.get('amount') and Decimal(fields['amount']) != Decimal(total):
            warnings.append('PI明确总金额与可读明细合计不一致，已保留明确总金额，请对照原件核对。')
        fields['amount'] = total
    elif len(explicit_totals) > 1:
        fields.pop('amount', None)
        warnings.append('PI包含多个不同的明确总金额，请人工确认，不自动取其中一个。')
    if len(currencies) == 1:
        fields['currency'] = currencies.pop().upper()
    match = re.search(r'Date\s*[:：]\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})', text, re.I)
    if match:
        for pattern in ('%B %d, %Y', '%B %d %Y', '%Y-%m-%d', '%Y/%m/%d'):
            try:
                fields['invoice_date'] = datetime.strptime(match[1], pattern).date().isoformat()
                break
            except ValueError:
                pass
    for key, label in (('buyer', '收货人'), ('buyer_address', '收货地址'), ('trade_terms', '贸易条款'), ('amount', '金额'), ('currency', '币种')):
        if not fields.get(key):
            warnings.append(label + '未能明确提取，请核对PI后填写')
    warnings.append('PI提取值须核对；商品计量单位未明确时留空，不从旧模板推测')
    return {'fields': fields, 'warnings': warnings}


def templates(root):
    return [{'id': key, 'name': name, 'ready': (Path(root) / 'private/lantern_templates' / (key + '.xlsx')).is_file()}
            for key, name in COMPANIES.items()]


def set_cell(sheet, address, value, numeric=False):
    main = '{' + NS['s'] + '}'
    data = sheet.find('s:sheetData', NS)
    number = re.search(r'\d+', address).group()
    row = next((r for r in data if r.get('r') == number), None)
    if row is None:
        row = ET.SubElement(data, main + 'row', {'r': number})
        data[:] = sorted(data, key=lambda x: int(x.get('r')))
    cell = next((c for c in row if c.get('r') == address), None)
    if cell is None:
        cell = ET.SubElement(row, main + 'c', {'r': address})
        row[:] = sorted(row, key=lambda x: column_index(x.get('r')))
    for child in list(cell):
        cell.remove(child)
    if value is None or value == '':
        cell.attrib.pop('t', None)
        return
    if numeric:
        cell.set('t', 'n')
        ET.SubElement(cell, main + 'v').text = decimal_text(value)
    else:
        cell.set('t', 'inlineStr')
        inline = ET.SubElement(cell, main + 'is')
        ET.SubElement(inline, main + 't', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}).text = str(value)


def generate(root, template_id, values):
    if template_id not in COMPANIES or not isinstance(values, dict) or set(values) - FIELDS:
        raise ValueError('公司模板或填写字段无效')
    if any(isinstance(value, bool) or not isinstance(value, (str, int, float, type(None))) for value in values.values()):
        raise ValueError('资料字段需为文字或数字')
    fields = {key: ('' if value is None else str(value)).strip() for key, value in values.items()}
    if any(len(value) > 4000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value) for value in fields.values()):
        raise ValueError('填写内容过长或包含控制字符')
    for key in ('buyer', 'buyer_address', 'trade_terms', 'currency', 'amount', 'product_description', 'quantity', 'packages', 'gross_weight', 'net_weight', 'volume'):
        if not fields.get(key):
            raise ValueError('请先核对并填写收货信息、条款、币种金额、品名数量和四项装箱合计')
    for key in ('amount', 'packages', 'gross_weight', 'net_weight', 'volume', 'quantity'):
        if fields.get(key):
            fields[key] = decimal_text(fields[key])
    if Decimal(fields['packages']) != Decimal(fields['packages']).to_integral_value() or Decimal(fields['packages']) < 1:
        raise ValueError('总件数需为正整数')
    if fields.get('weight_unit') != 'kg' or fields.get('volume_unit') != 'm3':
        raise ValueError('请先确认重量单位为公斤、体积单位为立方米；其他单位须核对换算后填写')
    if Decimal(fields['net_weight']) > Decimal(fields['gross_weight']):
        raise ValueError('净重不能大于毛重，请核对装箱数据')
    if not re.fullmatch('[A-Z]{3}', fields['currency']):
        raise ValueError('币种需为明确的三位英文代码')
    path = Path(root) / 'private/lantern_templates' / (template_id + '.xlsx')
    if not path.is_file():
        raise ValueError('本机尚未导入该公司模板，请由部署Codex导入用户提供的模板')
    with safe_zip(path.read_bytes()) as package:
        parts = workbook_parts(package)
        if not {'发票', '合同', '箱单', '报关单', '申报要素'}.issubset(parts):
            raise ValueError('模板缺少已核对的五个工作表，不猜测其它模板栏位')
        v = lambda key: fields.get(key, '')
        quantity, amount = v('quantity'), v('amount')
        unit_price = format((Decimal(amount) / Decimal(quantity)).quantize(Decimal('0.0001')), 'f') if quantity and Decimal(quantity) else ''
        method = re.search(r'\b(?:T/T|L/C)\b', v('payment_terms'), re.I)
        payment_short = method[0] if method else v('payment_terms')
        mapping = {
            '发票': {'D3':v('buyer'),'D4':v('buyer_address'),'B5':v('invoice_no'),'D5':v('invoice_date'),'B6':'','D6':'',
                   'B7':v('trade_terms'),'D7':payment_short,'B9':v('product_description'),'C9':quantity,'D9':unit_price,'E9':amount,
                   'B10':'','E10':'','C11':quantity,'E11':amount,'D8':f'UNIT PRICE ({v("currency")})\n单价','E8':f'AMOUNT ({v("currency")})\n总价'},
            '合同': {'C3':v('buyer'),'F3':v('buyer_address'),'C4':v('invoice_date'),'F4':v('contract_no'),'C7':v('trade_terms'),
                   'A9':v('product_description'),'D9':quantity,'E9':unit_price,'G9':amount,'A10':'','G10':'','D11':quantity,'G11':amount,
                   'C15':v('payment_terms'),'A26':'The Buyer: '+v('buyer'),'E8':f'UNIT PRICE ({v("currency")})','G8':f'AMOUNT ({v("currency")})'},
            '箱单': {'F3':v('invoice_no'),'F4':v('invoice_date'),'F5':v('contract_no'),'E6':v('buyer'),'E7':v('buyer_address'),
                   'B8':'','F8':'','B9':v('trade_terms'),'F9':payment_short,'B12':v('product_description'),'C12':quantity,
                   'D12':v('packages'),'E12':v('gross_weight'),'F12':v('net_weight'),'G12':v('volume'),
                   'C18':quantity,'D18':v('packages'),'E18':v('gross_weight'),'F18':v('net_weight'),'G18':v('volume'),
                   'C19':v('quantity_unit'),'D19':'PCS','E19':'KGS','F19':'KGS','G19':'CBM'},
            '报关单': {'A12':v('buyer'),'B12':'','D12':'','E12':'','B14':v('trade_terms'),'C14':'','E14':'','F14':'',
                    'A16':v('contract_no'),'B16':v('packages'),'C16':v('packing_type'),'E16':v('gross_weight'),'F16':v('net_weight'),
                    'A23':(v('hs_code')+' 品名：'+v('product_description')).strip(),'B23':(quantity+' '+v('quantity_unit')).strip(),
                    'C23':'','D23':unit_price,'E23':amount,'F23':v('currency')},
            '申报要素': {'A2':'HS:'+v('hs_code') if v('hs_code') else '', 'A3':'1.品名：'+v('product_description'),
                     'A4':'2.用途：','A5':'3.品牌：','A6':'4.型号：','A7':'5.材料：','A9':v('invoice_date')}
        }
        numeric = {'发票':{'C9','D9','E9','C11','E11'}, '合同':{'D9','E9','G9','D11','G11'},
                   '箱单':{'C12','D12','E12','F12','G12','C18','D18','E18','F18','G18'},
                   '报关单':{'B16','E16','F16','D23','E23'},'申报要素':set()}
        updated = {}
        styles = ET.fromstring(package.read('xl/styles.xml')) if 'xl/styles.xml' in package.namelist() else None
        xfs = styles.find('s:cellXfs', NS) if styles is not None else None
        currency_format = None
        if styles is not None and v('currency') != 'USD':
            formats = styles.find('s:numFmts', NS)
            if formats is None:
                formats = ET.Element('{'+NS['s']+'}numFmts'); styles.insert(0, formats)
            currency_format = max([163] + [int(x.get('numFmtId')) for x in formats]) + 1
            ET.SubElement(formats, '{'+NS['s']+'}numFmt', {'numFmtId':str(currency_format),'formatCode':'"'+v('currency')+'" #,##0.00'})
            formats.set('count', str(len(formats)))
        monetary = {'发票':{'D9','E9','E11'},'合同':{'E9','G9','G11'},'报关单':{'D23','E23'}}
        print_areas = {'发票':'$A$1:$E$15','合同':'$A$1:$I$27','箱单':'$A$1:$G$19',
                       '报关单':'$A$1:$H$49','申报要素':'$A$1:$A$9'}
        for name, changes in mapping.items():
            tree = ET.fromstring(package.read(parts[name]))
            for address, value in changes.items():
                set_cell(tree, address, value, address in numeric[name])
            for address in changes:
                wrap = address in {'发票':{'D4'},'合同':{'F3'},'箱单':{'E7'}}.get(name,set())
                money = currency_format is not None and address in monetary.get(name,set())
                if xfs is not None and (wrap or money):
                    cell = tree.find('.//s:c[@r="'+address+'"]', NS)
                    xf = copy.deepcopy(xfs[int(cell.get('s','0'))])
                    if wrap:
                        alignment = xf.find('s:alignment', NS)
                        if alignment is None:
                            alignment = ET.SubElement(xf, '{'+NS['s']+'}alignment')
                        alignment.set('wrapText','1'); alignment.set('vertical','top')
                        xf.set('applyAlignment','1')
                        row = tree.find('.//s:row[@r="'+re.search(r'\d+',address).group()+'"]', NS)
                        row.set('ht',str(max(90,float(row.get('ht','0'))))); row.set('customHeight','1')
                    if money:
                        xf.set('numFmtId',str(currency_format)); xf.set('applyNumberFormat','1')
                    cell.set('s',str(len(xfs))); xfs.append(xf); xfs.set('count',str(len(xfs)))
            setup = tree.find('s:pageSetup', NS)
            if setup is not None:
                setup.attrib.pop('scale', None)
                setup.set('fitToWidth', '1'); setup.set('fitToHeight', '1')
            props = tree.find('s:sheetPr', NS)
            if props is None:
                props = ET.Element('{'+NS['s']+'}sheetPr'); tree.insert(0, props)
            page_props = props.find('s:pageSetUpPr', NS)
            if page_props is None:
                page_props = ET.SubElement(props, '{'+NS['s']+'}pageSetUpPr')
            page_props.set('fitToPage', '1')
            updated[parts[name]] = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
        if styles is not None:
            updated['xl/styles.xml'] = ET.tostring(styles, encoding='utf-8', xml_declaration=True)
        workbook = ET.fromstring(package.read('xl/workbook.xml'))
        names = workbook.find('s:definedNames', NS)
        if names is None:
            names = ET.Element('{'+NS['s']+'}definedNames')
            index = next((i for i, item in enumerate(workbook) if item.tag.endswith(('}calcPr','}oleSize','}customWorkbookViews','}extLst'))), len(workbook))
            workbook.insert(index, names)
        for item in list(names):
            if item.get('name') == '_xlnm.Print_Area':
                names.remove(item)
        for index, sheet in enumerate(workbook.findall('s:sheets/s:sheet', NS)):
            name = sheet.get('name')
            if name in print_areas:
                ET.SubElement(names, '{'+NS['s']+'}definedName', {'name':'_xlnm.Print_Area','localSheetId':str(index)}).text = "'"+name+"'!"+print_areas[name]
        calc = workbook.find('s:calcPr', NS)
        if calc is None:
            calc = ET.SubElement(workbook, '{'+NS['s']+'}calcPr')
        calc.set('fullCalcOnLoad','1'); calc.set('forceFullCalc','1')
        updated['xl/workbook.xml'] = ET.tostring(workbook, encoding='utf-8', xml_declaration=True)
        output = io.BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for entry in package.infolist():
                archive.writestr(entry, updated.get(entry.filename, package.read(entry)))
        return output.getvalue()
