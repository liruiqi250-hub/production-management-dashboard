"""固定生产资料群附件读单建档；不执行公式，不推断缺项或覆盖人工进度。"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile
from xml.etree import ElementTree as ET

from app.lantern_documents import NS, safe_zip, workbook_parts, column_index
from app.production import Production, STAGES
from app.store import now

MAX_BYTES = 100 * 1024 * 1024
MAX_PIECES = 10000
SOURCE_ID = "production_01"
SOURCE_GROUP = "生产资料群"
FIELDS = ("buyer", "order_no", "business_contact", "followup", "voltage", "plug", "startup", "order_date", "delivery_date", "requirements")
FIELD_NAMES = dict(zip(FIELDS, ("买方/客户", "生产单号", "业务员", "跟单员", "电压", "插头/电气", "启动方式", "下单日期", "出货日期", "其他要求")))
LABELS = {
    "buyer": ("买方信息", "买方", "客户名称", "客户", "购买方"),
    "business_contact": ("业务员", "业务对接人", "业务负责人"),
    "followup": ("跟单员", "跟单人", "跟单"),
    "voltage": ("电压", "使用电压"),
    "plug": ("插头，电气", "插头、电气", "插头", "插头类型", "插头标准"),
    "startup": ("启动方式", "启动模式", "控制方式"),
    "order_date": ("下单日期", "订单日期", "收单日期"),
    "delivery_date": ("出货日期", "交货日期", "交货时间", "完工日期"),
    "requirements": ("备注", "特殊要求", "其他补充说明", "其他说明"),
}
HEADERS = {
    "sequence": ("序号", "编号"), "name": ("产品名称", "产品中文名称", "恐龙名称", "产品名字", "产品名", "名称", "名字"),
    "quantity": ("产品数量", "数量", "件数"), "unit": ("单位名称", "单位"),
    "specification": ("规格尺寸", "规格", "产品尺寸", "尺寸", "长宽高", "曲线长度", "长度"),
    "motions": ("恐龙动态功能配置", "动态功能配置", "功能配置", "动作", "动态", "动作要求"),
    "process": ("核心工艺参数", "工艺参数", "制作要求", "工艺要求"),
    "material": ("材质要求", "材料要求", "材质", "材料"),
}
ORDER_NO = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]{1,9}[-－—]\d{4}[-－—]\d{1,2}[-－—][A-Za-z0-9]+[-－—]\d+(?![A-Za-z0-9])", re.I)


def _text(value):
    return str(value or "").strip()


def _compact(value):
    return re.sub(r"[\s:：/、（）()，,]+", "", value).casefold()


def _check(checks, code, message, source_ref=""):
    checks.append({"code": code, "message": message, "source_ref": source_ref})


def _quantity(value):
    match = re.fullmatch(r"([1-9]\d*)(?:\.0+)?\s*(个|件|台|套|组|只|条|头)?", value.strip())
    if not match:
        return None, ""
    number = int(match[1])
    return (number if number <= MAX_PIECES else None), match[2] or ""


def _piece_specifications(spec, quantity, unit, checks, source_ref):
    if quantity is None:
        return []
    if not spec:
        return [""] * quantity
    # 仅明确的“规格文本 + 数量单位”分组能够分配到逐件，不从形容词或组内数字拆实物。
    blocks = [x.strip() for x in re.split(r"(?:\n\s*[-－—]{3,}\s*\n|[；;])", spec) if x.strip()]
    counts = []
    for block in blocks:
        matches = list(re.finditer(r"(?<![\d.])([1-9]\d*)\s*(个|件|台|套|组|只|条|头)(?![\u4e00-\u9fff])", block))
        if len(matches) != 1:
            counts = []
            break
        match = matches[0]
        size = (block[:match.start()] + block[match.end():]).strip(" \r\n，,：:")
        if not size or (unit and match[2] != unit):
            counts = []
            break
        counts.append((size, int(match[1])))
    if counts:
        if sum(n for _, n in counts) != quantity:
            _check(checks, "spec_quantity_conflict", f"规格分配合计{sum(n for _, n in counts)}{unit}，原数量{quantity}{unit}；逐件尺寸留空", source_ref)
            return [""] * quantity
        return [size for size, number in counts for _ in range(number)]
    mentions = list(re.finditer(r"(?<![\d.])([1-9]\d*)\s*(个|件|台|套|组|只|条|头)", spec))
    if len(blocks) > 1 or len(mentions) > 1 or "&" in spec or "及" in spec and re.search(r"\d", spec):
        _check(checks, "spec_assignment_unclear", "多规格未能明确分配到每件，逐件尺寸留空", source_ref)
        return [""] * quantity
    return [spec] * quantity


def _read_workbook(data):
    result = []
    with safe_zip(data) as package:
        if any(name.lower().endswith("vbaproject.bin") for name in package.namelist()):
            raise ValueError("不接收包含宏的生产单")
        strings = []
        if "xl/sharedStrings.xml" in package.namelist():
            strings = ["".join(n.text or "" for n in item.findall(".//s:t", NS)) for item in ET.fromstring(package.read("xl/sharedStrings.xml"))]
        for name, part in workbook_parts(package).items():
            sheet = ET.fromstring(package.read(part))
            rows = []
            for row in sheet.findall("s:sheetData/s:row", NS):
                if int(row.get("r", "0")) > 10000:
                    raise ValueError("生产单超过10000行读取范围")
                cells = {}
                for cell in row.findall("s:c", NS):
                    address = cell.get("r", "")
                    if not re.fullmatch(r"[A-Z]+[1-9]\d*", address) or column_index(address) >= 100:
                        raise ValueError("生产单单元格超出读取范围")
                    formula = cell.find("s:f", NS) is not None
                    if formula:
                        value = ""  # 缓存值可能过期；既不计算也不调用Office执行公式。
                    elif cell.get("t") == "inlineStr":
                        value = "".join(x.text or "" for x in cell.findall(".//s:t", NS))
                    else:
                        value = cell.findtext("s:v", "", NS)
                        if cell.get("t") == "s":
                            value = strings[int(value)]
                    cells[column_index(address)] = {"value": value.strip(), "source_ref": name + "!" + address, "formula": formula}
                if cells:
                    rows.append(cells)
            result.append((name, rows))
    return result


def parse_order(filename, data):
    """读取原单字段与产品，返回可保留来源的公开记录，不访问任何外部资源。"""
    fields = dict.fromkeys(FIELDS, "")
    field_sources, products, checks = {}, [], []
    declared_total = None
    sheets = _read_workbook(data)
    matched = False
    for _, rows in sheets:
        header_index, columns = None, {}
        for index, row in enumerate(rows):
            mapped = {}
            for col, cell in row.items():
                value = _compact(cell["value"])
                for key, aliases in HEADERS.items():
                    if key in mapped:
                        continue
                    if any(value == _compact(a) or (len(a) >= 4 and value.startswith(_compact(a))) for a in aliases):
                        mapped[key] = col
            if {"name", "quantity"} <= mapped.keys():
                header_index, columns = index, mapped
                break
        if header_index is None:
            continue
        matched = True
        for row in rows[:header_index]:
            items = sorted(row.items())
            for position, (col, cell) in enumerate(items):
                value = cell["value"]
                if not value:
                    continue
                if re.search(r"生产单(?:编号|号)|订单(?:编号|号)", value):
                    matches = ORDER_NO.findall(value)
                    found = matches[-1].replace("－", "-").replace("—", "-") if matches else ""
                    if not found:
                        split = re.split(r"[:：]", value, maxsplit=1)
                        found = split[1].strip() if len(split) == 2 else ""
                    if found:
                        if fields["order_no"] and fields["order_no"].casefold() != found.casefold():
                            _check(checks, "order_number_conflict", "原单存在多个不同订单号，保留首处原文", cell["source_ref"])
                        else:
                            fields["order_no"], field_sources["order_no"] = found, cell["source_ref"]
                for key, aliases in LABELS.items():
                    for label in aliases:
                        match = re.match(r"^" + re.escape(label) + r"\s*[:：]\s*([\s\S]*)$", value)
                        exact = re.sub(r"\s+", "", value) == re.sub(r"\s+", "", label)
                        if not match and not exact:
                            continue
                        content = match[1].strip() if match else ""
                        source = cell["source_ref"]
                        if not content and (exact or key == "requirements") and position + 1 < len(items):
                            adjacent = items[position + 1][1]
                            if adjacent["value"] and not re.search(r"[:：]", adjacent["value"]):
                                content, source = adjacent["value"], adjacent["source_ref"]
                        if content and not fields[key]:
                            fields[key], field_sources[key] = content, source
                        break
        def cell_for(row, key):
            return row.get(columns.get(key), {"value": "", "source_ref": "", "formula": False})
        for row in rows[header_index + 1:]:
            name_cell, qty_cell = cell_for(row, "name"), cell_for(row, "quantity")
            name = name_cell["value"]
            if any(re.match(r"^(?:总数|总计|合计|共计|TOTAL)\s*[:：]?$", c["value"], re.I) for c in row.values()):
                declared_total = {"value": _quantity(qty_cell["value"])[0], "raw_value": qty_cell["value"], "source_ref": qty_cell["source_ref"]}
                break
            if not name or re.match(r"^(?:其他|补充|备注|说明|要求)", name):
                continue
            raw_quantity = qty_cell["value"]
            quantity, embedded_unit = _quantity(raw_quantity)
            unit_cell = cell_for(row, "unit")
            unit = unit_cell["value"] or embedded_unit
            if quantity is None:
                _check(checks, "invalid_quantity", f"产品“{name}”数量为空、公式或非正整数，数量留空", qty_cell["source_ref"])
            if embedded_unit and unit_cell["value"] and embedded_unit != unit_cell["value"]:
                _check(checks, "quantity_unit_conflict", "数量内单位与单位列不同，保留单位列原值", unit_cell["source_ref"])
            item = {key: cell_for(row, key)["value"] for key in ("sequence", "specification", "motions", "process", "material")}
            item.update(name=name, quantity=quantity, unit=unit, source_ref=name_cell["source_ref"],
                        quantity_source_ref=qty_cell["source_ref"], field_sources={key: cell_for(row, key)["source_ref"] for key in columns})
            item["piece_specifications"] = _piece_specifications(item["specification"], quantity, unit, checks, cell_for(row, "specification")["source_ref"])
            # 同一附件PDF的页码在动态与工艺列发生冲突时，只记录原文差异。
            pages = [set(re.findall(r"(?:PDF|pdf)[^。\n]{0,18}?(\d+\s*[-—－~～]\s*\d+|\d+)\s*页", item[key])) for key in ("motions", "process")]
            if all(pages) and pages[0] != pages[1]:
                _check(checks, "attachment_page_conflict", f"产品“{name}”动作与工艺的附件页码不同", name_cell["source_ref"])
            products.append(item)
        # 一个订单主表已匹配；后续附件或重复打印页不再重复增加产品。
        break
    if not matched or not products:
        raise ValueError("未识别到包含产品名称与数量的生产单主表")
    file_numbers = ORDER_NO.findall(filename)
    if fields["order_no"] and file_numbers and fields["order_no"].casefold() != file_numbers[-1].casefold():
        _check(checks, "filename_order_number_conflict", f"文件名订单号{file_numbers[-1]}与表内订单号{fields['order_no']}不同，分别保留原文", field_sources.get("order_no", ""))
    totals = dict(Counter())
    for product in products:
        if product["quantity"] is not None:
            totals[product["unit"]] = totals.get(product["unit"], 0) + product["quantity"]
    if len(totals) > 1:
        _check(checks, "mixed_units", "存在不同数量单位，按单位分别统计，不合并为实物总件数", "")
    if declared_total and declared_total["value"] is not None:
        if any(p["quantity"] is None for p in products):
            _check(checks, "total_check_incomplete", "明细有空白或异常数量，汇总行与明细的算术检查不完整", declared_total["source_ref"])
        elif declared_total["value"] != sum(totals.values()):
            suffix = "；不同单位仍分别统计，算术合计不表示实物总件数" if len(totals) > 1 else ""
            _check(checks, "total_quantity_conflict", f"汇总行原数{declared_total['value']}，明细数字相加{sum(totals.values())}，两者不同" + suffix, declared_total["source_ref"])
    for key in FIELDS:
        if not fields[key]:
            _check(checks, "missing_" + key, "原单未填写" + FIELD_NAMES[key] + "，保持空白", "")
    return {"fields": fields, "field_sources": field_sources, "products": products, "checks": checks, "totals_by_unit": totals, "declared_total": declared_total}


class ProductionOrderIntake:
    def __init__(self, store):
        self.store = store
        Production(store)
        self.sources = store.private / "production_order_sources"
        with store.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS production_order_documents(
              id TEXT PRIMARY KEY,order_id TEXT NOT NULL,filename TEXT NOT NULL,
              sha256 TEXT NOT NULL UNIQUE,source_ref TEXT NOT NULL,created_at TEXT NOT NULL,
              storage_path TEXT NOT NULL,record_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS production_document_order ON production_order_documents(order_id,created_at);
            CREATE TABLE IF NOT EXISTS production_order_intake_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT NOT NULL,source_ref TEXT NOT NULL,
              filename TEXT NOT NULL,status TEXT NOT NULL,message TEXT NOT NULL);
            """)

    def details(self, order_id):
        with self.store.connect() as conn:
            rows = conn.execute("SELECT record_json FROM production_order_documents WHERE order_id=? ORDER BY created_at DESC,rowid DESC", (order_id,)).fetchall()
        records = [json.loads(row["record_json"]) for row in rows]
        return {"documents": records, "latest": records[0] if records else None}

    def source_path(self, document_id):
        with self.store.connect() as conn:
            row = conn.execute("SELECT storage_path FROM production_order_documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            raise ValueError("生产单原件不存在")
        path = (self.store.private / row["storage_path"]).resolve()
        if not path.is_relative_to(self.sources.resolve()) or not path.is_file():
            raise ValueError("生产单原件不存在")
        return path

    def import_file(self, path, source_ref="", actor="自动建档", order_id=None, *, original_filename=None):
        path = Path(path)
        if path.suffix.casefold() != ".xlsx" or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
            raise ValueError("生产单须为100MB以内的XLSX文件")
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        filename = Path(original_filename or re.sub(r"^\d{10,16}_\d+_", "", path.name)).name
        record = parse_order(filename, data)
        self.sources.mkdir(parents=True, exist_ok=True)
        saved = self.sources / (sha + ".xlsx")
        if not saved.exists():
            temporary = saved.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(saved)
        timestamp, document_id = now(), "PODOC-" + uuid.uuid4().hex
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute("SELECT id,order_id,record_json FROM production_order_documents WHERE sha256=?", (sha,)).fetchone()
            if duplicate:
                if order_id and duplicate["order_id"] != order_id:
                    raise ValueError("该原单已归档到另一生产单")
                return {"order_id": duplicate["order_id"], "document_id": duplicate["id"], "created": False, "pieces_created": 0, "duplicate": True, "checks": json.loads(duplicate["record_json"])["checks"]}
            existing = conn.execute("SELECT * FROM production_orders WHERE id=?", (order_id,)).fetchone() if order_id else None
            if order_id and not existing:
                raise ValueError("生产单不存在")
            if not existing and source_ref:
                previous = conn.execute("SELECT order_id FROM production_order_documents WHERE source_ref=? ORDER BY rowid DESC LIMIT 1", (source_ref,)).fetchone()
                if previous:
                    existing = conn.execute("SELECT * FROM production_orders WHERE id=?", (previous["order_id"],)).fetchone()
            number = record["fields"]["order_no"]
            if not existing and number:
                existing = conn.execute("SELECT * FROM production_orders WHERE order_no=?", (number,)).fetchone()
                if not existing:
                    matches = conn.execute("SELECT * FROM production_orders WHERE order_no=? COLLATE NOCASE", (number,)).fetchall()
                    if len(matches) == 1:
                        existing = matches[0]
                    elif matches:
                        _check(record["checks"], "order_number_case_conflict", "已有多个仅大小写不同的生产单号，未擅自合并", record["field_sources"].get("order_no", ""))
            if not existing:
                # 旧人工单保留过原附件文件名时，只按唯一完整文件名衔接，禁止相似名称匹配。
                pattern = re.compile(r"(?:^|[\\/\s：:|\"'])" + re.escape(filename) + r"(?=$|[\s|；;，,\"'])", re.I)
                matches = [r for r in conn.execute("SELECT * FROM production_orders WHERE source_ref!=''") if pattern.search(r["source_ref"])]
                if len(matches) == 1:
                    existing = matches[0]
                elif matches:
                    _check(record["checks"], "source_filename_conflict", "多个旧档引用同名原件，未擅自合并", source_ref)
            created = existing is None
            if created:
                order_id = "ORDER-" + uuid.uuid4().hex
                if number and re.search(r"-\d+$", number):
                    canonical = lambda value: re.sub(r"-0*(\d+)$", lambda m: "-" + str(int(m[1])), value or "").casefold()
                    for other in conn.execute("SELECT id,order_no FROM production_orders WHERE order_no IS NOT NULL"):
                        if other["order_no"] != number and canonical(other["order_no"]) == canonical(number):
                            _check(record["checks"], "similar_order_number", "已有仅流水号前导零不同的订单，未擅自合并：" + other["order_no"], record["field_sources"].get("order_no", ""))
                received = record["fields"]["order_date"]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", received):
                    received = ""
                conn.execute("INSERT INTO production_orders(id,order_no,title,business_contact,received_on,created_at,created_by,source_ref) VALUES(?,?,?,?,?,?,?,?)", (order_id, number, Path(filename).stem[:160], record["fields"]["business_contact"][:80], received, timestamp, actor, source_ref[:300]))
            else:
                order_id = existing["id"]
                if number and existing["order_no"] and number.casefold() != existing["order_no"].casefold():
                    _check(record["checks"], "associated_order_number_conflict", "已关联生产单编号与本原单不同，主档编号和人工记录保留", "")
            count = conn.execute("SELECT count(*) FROM production_pieces WHERE order_id=?", (order_id,)).fetchone()[0]
            pieces_created = 0
            # 新版只追加原件版本；已有逐件档案不重建、不删除、不覆盖工序和交付。
            if not count:
                for index, product in enumerate(record["products"], 1):
                    qty = product["quantity"]
                    if qty is None:
                        continue
                    if pieces_created + qty > MAX_PIECES:
                        _check(record["checks"], "piece_limit", "逐件数量超过单批上限，剩余产品保留在详情中", product["source_ref"])
                        break
                    for ordinal in range(1, qty + 1):
                        piece_id = "PIECE-" + uuid.uuid4().hex
                        label = f"{index:02d}-{ordinal:03d}" + ("组" if product["unit"] == "组" else "")
                        reference = (source_ref + "|" if source_ref else "") + product["source_ref"]
                        conn.execute("INSERT INTO production_pieces(id,order_id,piece_label,product_name,delivery_state,created_at,created_by,source_ref) VALUES(?,?,?,?,?,?,?,?)", (piece_id, order_id, label, product["name"][:160], "未安排", timestamp, actor, reference[:300]))
                        conn.executemany("INSERT INTO production_stages(piece_id,stage,state) VALUES(?,?,'未核对')", [(piece_id, name) for name in STAGES])
                        pieces_created += 1
            else:
                expected = sum(p["quantity"] or 0 for p in record["products"])
                if expected != count:
                    _check(record["checks"], "existing_piece_count_difference", f"原单本次数量{expected}，既有逐件记录{count}；保留已有工序与交付，未重建逐件档案", "")
            record.update(id=document_id, order_id=order_id, filename=filename, sha256=sha, source_ref=source_ref, created_at=timestamp)
            conn.execute("INSERT INTO production_order_documents VALUES(?,?,?,?,?,?,?,?)", (document_id, order_id, filename, sha, source_ref, timestamp, saved.relative_to(self.store.private).as_posix(), json.dumps(record, ensure_ascii=False)))
            Production._audit(conn, actor, "import_production_order_document", order_id, {"document_id": document_id, "sha256": sha, "created": created, "pieces_created": pieces_created, "checks": len(record["checks"])})
        return {"order_id": order_id, "document_id": document_id, "created": created, "pieces_created": pieces_created, "duplicate": False, "checks": record["checks"]}

    def _event(self, source_ref, filename, status, message):
        with self.store.connect() as conn:
            conn.execute("INSERT INTO production_order_intake_events(time,source_ref,filename,status,message) VALUES(?,?,?,?,?)", (now(), source_ref, filename, status, message))

    def ingest_manifest(self, manifest, actor="自动建档"):
        summary = {"created": 0, "imported": 0, "duplicates": 0, "pieces_created": 0, "skipped": 0, "errors": [], "results": []}
        if manifest.get("status") != "exported" or not manifest.get("sensitive_cache_cleanup"):
            raise ValueError("生产单建档需要已完整采集并清理敏感缓存的来源")
        allowed = [row for row in manifest.get("export_results", []) if row.get("source_id") == SOURCE_ID and row.get("group") == SOURCE_GROUP and row.get("binding_verified") is True]
        if len(allowed) != 1:
            raise ValueError("生产资料群白名单来源未唯一核验")
        row = allowed[0]
        if row.get("status") == "verified_empty_range":
            return summary
        try:
            if row.get("status") != "exported":
                raise ValueError("生产资料群采集未成功")
            source = Path(row.get("source_json", "")).resolve()
            if not source.is_relative_to(self.store.private.resolve()) or not source.is_file() or source.stat().st_size > 30 * 1024 * 1024:
                raise ValueError("生产资料群消息文件不在本机私有采集范围")
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row.get("source_sha256"):
                raise ValueError("生产资料群消息文件摘要不一致")
            messages = json.loads(raw.decode("utf-8-sig"))
            if messages.get("chat_name") != SOURCE_GROUP:
                raise ValueError("生产资料群消息来源不符")
        except (ValueError, OSError, TypeError) as exc:
            summary["errors"].append({"source_ref": SOURCE_ID, "filename": "", "message": str(exc)})
            self._event(SOURCE_ID, "", "failed", str(exc))
            return summary
        for message in messages.get("messages", []):
            media = message.get("media") or {}
            if media.get("kind") != "file" or media.get("available") is not True:
                continue
            filename = str(media.get("name") or Path(str(media.get("path") or "")).name)
            if Path(filename).suffix.casefold() != ".xlsx" or not re.search(r"生产[任任务]*单|订单", filename):
                summary["skipped"] += 1
                continue
            ref = SOURCE_ID + "|" + str(message.get("source_db") or "") + ":" + str(message.get("local_id") or "")
            try:
                relative = Path(str(media.get("path") or ""))
                if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[:2] != ("media", "files"):
                    raise ValueError("附件路径超出该群显式导出文件范围")
                attachment = (source.parent / relative).resolve()
                if not attachment.is_relative_to(source.parent.resolve() / "media" / "files") or not attachment.is_file():
                    raise ValueError("附件路径超出该群显式导出文件范围或原件缺失")
                result = self.import_file(attachment, ref, actor, original_filename=filename)
                summary["results"].append(result)
                summary["duplicates"] += int(result["duplicate"])
                summary["imported"] += int(not result["duplicate"])
                summary["created"] += int(result["created"])
                summary["pieces_created"] += result["pieces_created"]
                self._event(ref, filename, "duplicate" if result["duplicate"] else "imported", result["document_id"])
            except (ValueError, OSError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError) as exc:
                error = {"source_ref": ref, "filename": filename, "message": str(exc)}
                summary["errors"].append(error)
                self._event(ref, filename, "failed", str(exc))
        return summary
