"""彩灯独立台账；订单留存与人工登记，不自动推断完成或订单关系。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
import uuid
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from app.store import Store, now, password_hash

STAGES = ("丝架", "装灯", "裱糊", "上色")
STAGE_STATUSES = {"未开始", "进行中", "已完成"}
PROJECT_FIELDS = {
    "name": "项目名称", "order_no": "订单编号", "country": "国家", "voltage": "电压",
    "completion_date": "完成日期", "salesperson": "业务员", "merchandiser": "跟单员",
    "source_attachment": "原生产单附件", "notes": "备注",
}
SHIPMENT_FIELDS = {
    "country": "国家", "customer": "客户名称", "documents": "清关资料、电放提单提供情况",
    "order_status": "订单进度", "trade_terms": "贸易条款",
    "payment": "付款情况", "quantity": "数量", "loading_date": "装货时间", "route": "路线",
    "container_no": "柜号", "shipping_line": "船司", "container_type": "箱型", "forwarder": "货代公司",
    "etd": "ETD", "eta": "ETA", "salesperson": "业务员", "merchandiser": "跟单员",
    "document_owner": "资料制作员", "customs_delivery": "是否清关派送", "product_category": "产品类别", "notes": "备注",
    "order_no": "订单编号", "project_id": "关联项目ID",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}
SETUP = {"wechat": "待提供群清单", "email": "阿里企业邮箱待配置", "templates": "待提供固定模板"}


class RevisionConflict(ValueError):
    """浏览器仍持有旧版本，不能覆盖其他人的修改。"""


def text(value, label, limit=300):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
        raise ValueError(f"{label}需为不超过{limit}字的文字")
    return value.strip()


def checked_date(value, label):
    value = text(value, label, 10)
    if value:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError as exc:
            raise ValueError(f"{label}需为有效的YYYY-MM-DD日期，未知时留空") from exc
    return value


def csv_cell(value):
    value = "" if value is None else str(value)
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def spreadsheet_bytes(rows, *, xlsx=False):
    """xlsx 使用文字单元格，CSV 对公式前缀加保护，均不生成可执行公式。"""
    if not xlsx:
        stream = io.StringIO(newline="")
        csv.writer(stream).writerows([[csv_cell(v) for v in row] for row in rows])
        return stream.getvalue().encode("utf-8-sig")
    def column(number):
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result
    body = []
    for number, row in enumerate(rows, 1):
        cells = []
        for index, value in enumerate(row, 1):
            value = escape("" if value is None else str(value))
            cells.append(f'<c r="{column(index)}{number}" t="inlineStr"><is><t xml:space="preserve">{value}</t></is></c>')
        body.append(f'<row r="{number}">' + "".join(cells) + "</row>")
    last_cell = f"{column(len(rows[0]))}{max(1, len(rows))}"
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<dimension ref="A1:{last_cell}"/><sheetViews><sheetView workbookViewId="0">'
             '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
             '</sheetView></sheetViews><sheetData>' + "".join(body) + f'</sheetData><autoFilter ref="A1:{last_cell}"/></worksheet>')
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="彩灯台账" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value.encode("utf-8"))
    return output.getvalue()


class LanternStore(Store):
    def __init__(self, workspace):
        super().__init__(workspace, database_name="lantern.sqlite3")
        self.uploads = self.private / "lantern_uploads"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS lantern_projects(
                  id TEXT PRIMARY KEY,data_json TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lantern_shipments(
                  id TEXT PRIMARY KEY,data_json TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lantern_attachments(
                  id TEXT PRIMARY KEY,filename TEXT NOT NULL,suffix TEXT NOT NULL,bytes INTEGER NOT NULL,
                  sha256 TEXT NOT NULL,created_at TEXT NOT NULL,username TEXT NOT NULL);
            """)

    def bootstrap_users(self):
        with self.connect() as conn:
            if conn.execute("SELECT count(*) FROM users").fetchone()[0]:
                return
            credentials = []
            for username, display, role in (("jianghong", "江红", "admin"),
                                             ("huangyumeng", "黄钰萌", "editor"),
                                             ("tianxiuling", "田秀玲", "editor")):
                password, salt = secrets.token_urlsafe(18), secrets.token_hex(16)
                conn.execute("INSERT INTO users(username,display_name,role,salt,password_hash,active,must_change_password) VALUES(?,?,?,?,?,1,1)",
                             (username, display, role, salt, password_hash(password, salt)))
                credentials.append(f"{display}（{'管理员' if role == 'admin' else '跟单处理'}）\n账号：{username}\n初始密码：{password}\n")
            note = ("彩灯部门看板本机独立账号\n\n" + "\n".join(credentials)
                    + "\n首次登录必须各自修改本人密码。此文件只留本机，不上传、不转发。\n")
            (self.private / "彩灯初始账号.txt").write_text(note, encoding="utf-8")

    def snapshot(self):
        with self.connect() as conn:
            analysis = conn.execute("SELECT value_json FROM metadata WHERE key='lantern_analysis'").fetchone()
            return {
                "projects": [json.loads(row[0]) for row in conn.execute("SELECT data_json FROM lantern_projects ORDER BY updated_at DESC,id")],
                "shipments": [json.loads(row[0]) for row in conn.execute("SELECT data_json FROM lantern_shipments ORDER BY updated_at DESC,id")],
                "setup": dict(SETUP),
                "analysis": json.loads(analysis[0]) if analysis else {"findings": [], "limitations": [], "updated_at": None},
            }

    def save_findings(self, job_id, findings, limitations, actor):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id) or not isinstance(findings, list) or len(findings) > 500:
            raise ValueError("整理结果标识或数量无效")
        if not isinstance(limitations, list) or any(not isinstance(x, str) or len(x) > 2000 for x in limitations):
            raise ValueError("分析边界需为文字列表")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = self._actor(conn, actor)
            for item in findings:
                if (not isinstance(item, dict) or set(item) != {"kind", "record_id", "summary", "evidence_refs"}
                        or item["kind"] not in {"project", "shipment", "general"}
                        or not isinstance(item["record_id"], str)
                        or not isinstance(item["summary"], str) or not 0 < len(item["summary"]) <= 2000
                        or not isinstance(item["evidence_refs"], list) or not item["evidence_refs"]
                        or any(not isinstance(ref, str) or not ref for ref in item["evidence_refs"])):
                    raise ValueError("整理发现不满足可追溯数据要求")
                if item["kind"] == "general":
                    if item["record_id"]:
                        raise ValueError("未匹配发现不能推测关联记录")
                elif not conn.execute("SELECT 1 FROM " + ("lantern_projects" if item["kind"] == "project" else "lantern_shipments")
                                      + " WHERE id=?", (item["record_id"],)).fetchone():
                    raise ValueError("整理发现关联的彩灯记录不存在")
            result = {"job_id": job_id, "findings": findings, "limitations": limitations,
                      "updated_at": now(), "requested_by": user["username"], "review_state": "待江红复核"}
            conn.execute("INSERT INTO metadata VALUES('lantern_analysis',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                         (json.dumps(result, ensure_ascii=False),))
            conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                         (now(), user["username"], "lantern_codex_analysis", job_id,
                          json.dumps({"findings": len(findings), "manual_states_preserved": True}, ensure_ascii=False)))
        return result

    @staticmethod
    def _actor(conn, actor):
        username = actor if isinstance(actor, str) else actor.get("username") if isinstance(actor, dict) else None
        row = conn.execute("SELECT username,display_name,role,must_change_password FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if not row or row["role"] not in {"admin", "editor"} or row["must_change_password"]:
            raise PermissionError("仅完成首次改密的处理人员或管理员可修改彩灯台账")
        return dict(row)

    def _project(self, data, previous, conn):
        result = {key: text(data.get(key, previous.get(key, "")), label, 4000 if key == "notes" else 300)
                  for key, label in PROJECT_FIELDS.items()}
        if not result["name"]:
            raise ValueError("请填写项目名称")
        result["completion_date"] = checked_date(result["completion_date"], "完成日期")
        attachment = result["source_attachment"]
        if attachment and not conn.execute("SELECT 1 FROM lantern_attachments WHERE id=?", (attachment,)).fetchone():
            raise ValueError("原生产单附件不存在，请先上传再关联")
        incoming = data.get("products", previous.get("products", []))
        if not isinstance(incoming, list) or len(incoming) > 500:
            raise ValueError("产品需为最多500项的列表")
        old = {p["id"]: p for p in previous.get("products", [])}
        seen, products = set(), []
        for item in incoming:
            if not isinstance(item, dict) or set(item) - {"id", "name", "specification", "quantity", "stages"}:
                raise ValueError("产品包含不支持的字段")
            supplied_id = item.get("id")
            if supplied_id is not None and (not isinstance(supplied_id, str) or supplied_id not in old):
                raise ValueError("产品ID只能引用本项目已有产品，新产品请留空ID")
            product_id = supplied_id or uuid.uuid4().hex
            if product_id in seen:
                raise ValueError("产品ID重复")
            seen.add(product_id)
            original = old.get(product_id, {})
            product = {"id": product_id,
                       "name": text(item.get("name", original.get("name", "")), "产品名称"),
                       "specification": text(item.get("specification", original.get("specification", "")), "产品规格", 1000)}
            if not product["name"]:
                raise ValueError("请填写每个产品的名称")
            quantity = item.get("quantity", original.get("quantity", ""))
            if quantity is None:
                quantity = ""
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float, str)):
                raise ValueError("产品数量需为非负数字，未知时留空")
            quantity = str(quantity).strip()
            if quantity and (len(quantity) > 30 or not re.fullmatch(r"\d+(?:\.\d+)?", quantity)):
                raise ValueError("产品数量需为非负数字，未知时留空")
            product["quantity"] = quantity
            supplied_stages = item.get("stages", {})
            if not isinstance(supplied_stages, dict) or set(supplied_stages) - set(STAGES):
                raise ValueError("仅支持丝架、装灯、裱糊、上色四个节点")
            product["stages"] = {}
            for stage in STAGES:
                value = supplied_stages.get(stage, {})
                if not isinstance(value, dict) or set(value) - {"status", "note"}:
                    raise ValueError("工序仅支持状态与处理说明")
                before = original.get("stages", {}).get(stage, {})
                status = value.get("status", before.get("status", "未开始"))
                note = text(value.get("note", before.get("note", "")), stage + "说明", 2000)
                if not isinstance(status, str) or status not in STAGE_STATUSES:
                    raise ValueError("工序状态仅支持未开始、进行中、已完成")
                if status == "已完成" and not note:
                    raise ValueError(f"{stage}已完成需填写实际完成说明")
                product["stages"][stage] = {"status": status, "note": note}
            products.append(product)
        result["products"] = products
        return result

    @staticmethod
    def _shipment(data, previous, conn):
        result = {key: text(data.get(key, previous.get(key, "")), label, 4000 if key in {"notes", "documents"} else 300)
                  for key, label in SHIPMENT_FIELDS.items()}
        if not result["country"] and not result["customer"] and not result["order_no"]:
            raise ValueError("请至少填写国家、客户或订单编号中的一项")
        for key in ("loading_date", "etd", "eta"):
            result[key] = checked_date(result[key], SHIPMENT_FIELDS[key])
        if result["project_id"] and not conn.execute("SELECT 1 FROM lantern_projects WHERE id=?", (result["project_id"],)).fetchone():
            raise ValueError("关联项目不存在，请选择已登记项目或留空")
        return result

    def save_project(self, data, actor, project_id=None):
        return self._save("projects", data, actor, project_id)

    def save_shipment(self, data, actor, shipment_id=None):
        return self._save("shipments", data, actor, shipment_id)

    def _save(self, kind, data, actor, item_id):
        fields = PROJECT_FIELDS if kind == "projects" else SHIPMENT_FIELDS
        allowed = set(fields) | ({"products"} if kind == "projects" else set())
        if item_id is not None:
            allowed.add("revision")
        if not isinstance(data, dict) or not data or set(data) - allowed:
            raise ValueError("台账包含不支持的字段，系统编号和时间不能手工修改")
        if item_id is not None and (type(data.get("revision")) is not int or data["revision"] < 1):
            raise ValueError("修改时必须提供当前revision版本号")
        if item_id is not None and set(data) == {"revision"}:
            raise ValueError("请至少修改一个台账字段")
        table = "lantern_" + kind
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = self._actor(conn, actor)
            previous = {}
            if item_id is not None:
                row = conn.execute(f"SELECT data_json,revision FROM {table} WHERE id=?", (item_id,)).fetchone()
                if not row:
                    raise KeyError("彩灯台账记录不存在")
                if row["revision"] != data["revision"]:
                    raise RevisionConflict("该记录已被其他人更新，请刷新后核对并重新保存")
                previous = json.loads(row["data_json"])
            result = self._project(data, previous, conn) if kind == "projects" else self._shipment(data, previous, conn)
            timestamp = now()
            result.update({"id": item_id or uuid.uuid4().hex, "revision": previous.get("revision", 0) + 1,
                           "created_at": previous.get("created_at", timestamp), "updated_at": timestamp,
                           "updated_by": user["username"], "updated_by_name": user["display_name"]})
            conn.execute(f"INSERT INTO {table}(id,data_json,revision,updated_at) VALUES(?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,revision=excluded.revision,updated_at=excluded.updated_at",
                         (result["id"], json.dumps(result, ensure_ascii=False), result["revision"], timestamp))
            conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                         (timestamp, user["username"], ("update_" if item_id else "create_") + "lantern_" + kind,
                          result["id"], json.dumps({"name": user["display_name"], "before": previous, "after": result}, ensure_ascii=False)))
        return result

    def upload_order(self, filename, content, actor):
        if (not isinstance(filename, str) or not filename or len(filename) > 180 or filename != filename.strip()
                or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', filename)
                or filename in {".", ".."} or filename.endswith((" ", "."))
                or re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", filename, re.I)):
            raise ValueError("附件名需为安全的文件名，不能含路径或控制字符")
        suffix = Path(filename).suffix.casefold()
        if suffix not in UPLOAD_EXTENSIONS:
            raise ValueError("仅支持PDF、Word、Excel、CSV、PNG或JPG原生产单")
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_UPLOAD_BYTES:
            raise ValueError("原生产单附件不能为空且不能超过10MB")
        result = {"id": uuid.uuid4().hex, "filename": filename, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        destination = self.uploads / (result["id"] + suffix)
        written = False
        try:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                user = self._actor(conn, actor)
                self.uploads.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as handle:
                    handle.write(content)
                written = True
                timestamp = now()
                conn.execute("INSERT INTO lantern_attachments VALUES(?,?,?,?,?,?,?)",
                             (result["id"], filename, suffix, result["bytes"], result["sha256"], timestamp, user["username"]))
                conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                             (timestamp, user["username"], "upload_lantern_order", result["id"], json.dumps(result, ensure_ascii=False)))
        except Exception:
            if written:
                destination.unlink(missing_ok=True)
            raise
        return result

    def order_attachment(self, attachment_id):
        if not re.fullmatch(r"[a-f0-9]{32}", attachment_id):
            raise KeyError("生产单附件不存在")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM lantern_attachments WHERE id=?", (attachment_id,)).fetchone()
        if not row:
            raise KeyError("生产单附件不存在")
        path = self.uploads / (row["id"] + row["suffix"])
        if not path.is_file():
            raise KeyError("生产单附件文件缺失，请联系维护人员")
        content = path.read_bytes()
        if len(content) != row["bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError("生产单附件校验失败，请联系维护人员")
        return dict(row), content

    def export(self, kind, *, xlsx=False):
        snapshot = self.snapshot()
        if kind == "projects":
            rows = [list(PROJECT_FIELDS.values()) + ["项目ID", "产品ID", "产品名称", "产品规格", "产品数量"]
                    + [stage + suffix for stage in STAGES for suffix in ("状态", "说明")] + ["修订号", "更新时间", "更新人"]]
            for project in snapshot["projects"]:
                for product in project["products"] or [{}]:
                    values = [project.get(key, "") for key in PROJECT_FIELDS]
                    values += [project["id"], product.get("id", ""), product.get("name", ""), product.get("specification", ""), product.get("quantity", "")]
                    values += [product.get("stages", {}).get(stage, {}).get(key, "") for stage in STAGES for key in ("status", "note")]
                    rows.append(values + [project["revision"], project["updated_at"], project["updated_by_name"]])
        elif kind == "shipments":
            rows = [["序号"] + list(SHIPMENT_FIELDS.values()) + ["出货ID", "修订号", "更新时间", "更新人"]]
            rows += [[index] + [shipment.get(key, "") for key in SHIPMENT_FIELDS]
                     + [shipment["id"], shipment["revision"], shipment["updated_at"], shipment["updated_by_name"]]
                     for index, shipment in enumerate(snapshot["shipments"], 1)]
        else:
            raise ValueError("导出台账类型无效")
        return spreadsheet_bytes(rows, xlsx=xlsx)
