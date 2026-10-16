"""生产单、逐件产品及人工复核的工序进度。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date

from app.store import now
from app.permissions import require_piece_edit

STAGES = ("机械图", "钳工（机械、钢架）", "海绵（造型）", "裱糊（纹理、表皮）", "刷胶", "上色（喷色）")
STATES = ("未核对", "未开始", "进行中", "待业务确认", "合格", "返工", "暂缓", "不适用")
DELIVERY = ("未安排", "待发货", "部分交付", "已交付", "暂缓")
BASES = ("业务口头放行", "厂内检查", "现场进展", "人工核对")


def field(value, label, limit=160, optional=False):
    if value is None and optional:
        return ""
    if not isinstance(value, str) or len(value.strip()) > limit or (not optional and not value.strip()):
        raise ValueError(f"{label}无效")
    return value.strip()


class Production:
    def __init__(self, store):
        self.store = store
        with store.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS production_orders(
              id TEXT PRIMARY KEY,order_no TEXT,title TEXT NOT NULL,business_contact TEXT NOT NULL,
              received_on TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,
              source_ref TEXT NOT NULL DEFAULT '');
            CREATE UNIQUE INDEX IF NOT EXISTS production_order_no_unique ON production_orders(order_no)
              WHERE order_no IS NOT NULL AND order_no!='';
            CREATE TABLE IF NOT EXISTS production_pieces(
              id TEXT PRIMARY KEY,order_id TEXT NOT NULL,piece_label TEXT NOT NULL,
              product_name TEXT NOT NULL,delivery_state TEXT NOT NULL DEFAULT '未安排',
              created_at TEXT NOT NULL,created_by TEXT NOT NULL,source_ref TEXT NOT NULL DEFAULT '',
              UNIQUE(order_id,piece_label));
            CREATE TABLE IF NOT EXISTS production_stages(
              piece_id TEXT NOT NULL,stage TEXT NOT NULL,state TEXT NOT NULL DEFAULT '未核对',
              note TEXT NOT NULL DEFAULT '',basis TEXT NOT NULL DEFAULT '',source_ref TEXT NOT NULL DEFAULT '',
              updated_at TEXT,updated_by TEXT,PRIMARY KEY(piece_id,stage));
            CREATE TABLE IF NOT EXISTS production_links(
              item_type TEXT NOT NULL,item_id TEXT NOT NULL,piece_id TEXT NOT NULL,
              linked_at TEXT NOT NULL,linked_by TEXT NOT NULL,PRIMARY KEY(item_type,item_id));
            CREATE TABLE IF NOT EXISTS production_updates(
              id INTEGER PRIMARY KEY AUTOINCREMENT,piece_id TEXT NOT NULL,kind TEXT NOT NULL,
              subject TEXT NOT NULL,old_value TEXT NOT NULL,new_value TEXT NOT NULL,
              note TEXT NOT NULL,basis TEXT NOT NULL,source_ref TEXT NOT NULL,
              updated_at TEXT NOT NULL,updated_by TEXT NOT NULL);
            """)
            for table in ("production_orders", "production_pieces"):
                if "source_ref" not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN source_ref TEXT NOT NULL DEFAULT ''")

    def snapshot(self):
        with self.store.connect() as conn:
            orders = [dict(r) for r in conn.execute("SELECT * FROM production_orders ORDER BY created_at DESC,rowid DESC")]
            pieces = [dict(r) for r in conn.execute("SELECT * FROM production_pieces ORDER BY created_at,rowid")]
            stages = [dict(r) for r in conn.execute("SELECT * FROM production_stages")]
            links = [dict(r) for r in conn.execute("SELECT item_type,item_id,piece_id FROM production_links")]
            updates = [dict(r) for r in conn.execute("SELECT * FROM production_updates ORDER BY id DESC")]
        stage_map = {(r["piece_id"], r["stage"]): r for r in stages}
        for piece in pieces:
            piece["stages"] = [stage_map.get((piece["id"], name), {"stage": name, "state": "未核对", "note": "", "basis": "", "source_ref": "", "updated_at": None, "updated_by": None}) for name in STAGES]
            piece["links"] = [r for r in links if r["piece_id"] == piece["id"]]
            piece["updates"] = [r for r in updates if r["piece_id"] == piece["id"]]
        for order in orders:
            order["pieces"] = [piece for piece in pieces if piece["order_id"] == order["id"]]
        return {"orders": orders, "stages": STAGES, "states": STATES, "delivery_states": DELIVERY,
                "policy": "业务员同意只使明确对应的单件与工序记为合格；其它工序、交付和售后各自记录。群聊候选不自动修改人工进度。"}

    def add_order(self, data, actor):
        title = field(data.get("title"), "生产单名称")
        order_no = field(data.get("order_no"), "生产单号", 80, optional=True)
        contact = field(data.get("business_contact"), "业务对接人", 80, optional=True)
        received = field(data.get("received_on"), "收单日期", 10, optional=True)
        source = field(data.get("source_ref"), "生产单来源", 300, optional=True)
        if received:
            try:
                if date.fromisoformat(received).isoformat() != received:
                    raise ValueError
            except ValueError:
                raise ValueError("收单日期格式无效") from None
        order_id, timestamp = "ORDER-" + uuid.uuid4().hex, now()
        with self.store.connect() as conn:
            try:
                conn.execute("INSERT INTO production_orders(id,order_no,title,business_contact,received_on,created_at,created_by,source_ref) VALUES(?,?,?,?,?,?,?,?)", (order_id, order_no, title, contact, received, timestamp, actor, source))
            except sqlite3.IntegrityError:
                raise ValueError("生产单号已存在") from None
            self._audit(conn, actor, "add_production_order", order_id, {"title": title, "order_no": order_no})
        return {"id": order_id}

    def add_piece(self, data, actor):
        order_id = field(data.get("order_id"), "生产单编号", 80)
        label = field(data.get("piece_label"), "单件编号", 80)
        name = field(data.get("product_name"), "产品名称")
        source = field(data.get("source_ref"), "单件来源", 300, optional=True)
        piece_id, timestamp = "PIECE-" + uuid.uuid4().hex, now()
        with self.store.connect() as conn:
            if not conn.execute("SELECT 1 FROM production_orders WHERE id=?", (order_id,)).fetchone():
                raise ValueError("生产单不存在")
            try:
                conn.execute("INSERT INTO production_pieces(id,order_id,piece_label,product_name,delivery_state,created_at,created_by,source_ref) VALUES(?,?,?,?,?,?,?,?)", (piece_id, order_id, label, name, "未安排", timestamp, actor, source))
            except sqlite3.IntegrityError:
                raise ValueError("该生产单内单件编号已存在") from None
            conn.executemany("INSERT INTO production_stages(piece_id,stage,state) VALUES(?,?,'未核对')", [(piece_id, name) for name in STAGES])
            self._audit(conn, actor, "add_production_piece", piece_id, {"order_id": order_id, "piece_label": label, "product_name": name})
        return {"id": piece_id}

    def update_order(self, order_id, data, actor):
        allowed = {"title", "order_no", "business_contact", "received_on", "source_ref"}
        if not data or set(data) - allowed:
            raise ValueError("生产单修改字段无效")
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM production_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise ValueError("生产单不存在")
            values = dict(row)
            values.update(data)
            values["title"] = field(values["title"], "生产单名称")
            for key, label, limit in [("order_no", "生产单号", 80), ("business_contact", "业务对接人", 80),
                                      ("received_on", "收单日期", 10), ("source_ref", "生产单来源", 300)]:
                values[key] = field(values[key], label, limit, optional=True)
            if values["received_on"]:
                try:
                    if date.fromisoformat(values["received_on"]).isoformat() != values["received_on"]:
                        raise ValueError
                except ValueError:
                    raise ValueError("收单日期格式无效") from None
            try:
                conn.execute("UPDATE production_orders SET order_no=?,title=?,business_contact=?,received_on=?,source_ref=? WHERE id=?",
                             (values["order_no"], values["title"], values["business_contact"], values["received_on"], values["source_ref"], order_id))
            except sqlite3.IntegrityError:
                raise ValueError("生产单号已存在") from None
            self._audit(conn, actor, "update_production_order", order_id, {key: [row[key], values[key]] for key in allowed if row[key] != values[key]})
        return {"ok": True}

    def update_piece(self, piece_id, data, actor):
        allowed = {"piece_label", "product_name", "source_ref"}
        if not data or set(data) - allowed:
            raise ValueError("单件修改字段无效")
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM production_pieces WHERE id=?", (piece_id,)).fetchone()
            if not row:
                raise ValueError("单件产品不存在")
            values = dict(row)
            values.update(data)
            values["piece_label"] = field(values["piece_label"], "单件编号", 80)
            values["product_name"] = field(values["product_name"], "产品名称")
            values["source_ref"] = field(values["source_ref"], "单件来源", 300, optional=True)
            try:
                conn.execute("UPDATE production_pieces SET piece_label=?,product_name=?,source_ref=? WHERE id=?",
                             (values["piece_label"], values["product_name"], values["source_ref"], piece_id))
            except sqlite3.IntegrityError:
                raise ValueError("该生产单内单件编号已存在") from None
            self._audit(conn, actor, "update_production_piece", piece_id, {key: [row[key], values[key]] for key in allowed if row[key] != values[key]})
        return {"ok": True}

    def update_stage(self, piece_id, data, actor):
        stage, state, basis = data.get("stage"), data.get("state"), data.get("basis")
        if stage not in STAGES or state not in STATES or basis not in BASES:
            raise ValueError("工序、状态或依据类型无效")
        note = field(data.get("note"), "进度说明", 2000, optional=True)
        source = field(data.get("source_ref"), "来源定位", 300, optional=True)
        if state in {"合格", "返工", "暂缓", "不适用"} and not note:
            raise ValueError("合格、返工、暂缓或不适用需要具体依据说明")
        if basis == "业务口头放行" and (state != "合格" or not source):
            raise ValueError("业务口头放行仅用于合格，须填写对应消息来源")
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            require_piece_edit(conn, actor, piece_id, self.store.workspace)
            previous = conn.execute("SELECT state FROM production_stages WHERE piece_id=? AND stage=?", (piece_id, stage)).fetchone()
            if not previous:
                raise ValueError("单件产品或工序不存在")
            timestamp = now()
            conn.execute("UPDATE production_stages SET state=?,note=?,basis=?,source_ref=?,updated_at=?,updated_by=? WHERE piece_id=? AND stage=?",
                         (state, note, basis, source, timestamp, actor, piece_id, stage))
            conn.execute("INSERT INTO production_updates(piece_id,kind,subject,old_value,new_value,note,basis,source_ref,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (piece_id, "工序", stage, previous["state"], state, note, basis, source, timestamp, actor))
            self._audit(conn, actor, "update_production_stage", piece_id, {"stage": stage, "state": state, "basis": basis, "source_ref": source})
        return {"ok": True}

    def update_delivery(self, piece_id, data, actor):
        state = data.get("state")
        if state not in DELIVERY:
            raise ValueError("交付状态无效")
        note = field(data.get("note"), "交付说明", 2000, optional=True)
        source = field(data.get("source_ref"), "交付来源", 300, optional=True)
        if state in {"部分交付", "已交付", "暂缓"} and not note:
            raise ValueError("交付或暂缓须填写实际依据")
        if state in {"部分交付", "已交付"} and not source:
            raise ValueError("部分交付或已交付须填写出货记录或消息来源")
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            require_piece_edit(conn, actor, piece_id, self.store.workspace)
            previous = conn.execute("SELECT delivery_state FROM production_pieces WHERE id=?", (piece_id,)).fetchone()
            if not previous:
                raise ValueError("单件产品不存在")
            timestamp = now()
            conn.execute("UPDATE production_pieces SET delivery_state=? WHERE id=?", (state, piece_id))
            conn.execute("INSERT INTO production_updates(piece_id,kind,subject,old_value,new_value,note,basis,source_ref,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (piece_id, "交付", "交付状态", previous["delivery_state"], state, note, "人工核对", source, timestamp, actor))
            self._audit(conn, actor, "update_production_delivery", piece_id, {"state": state, "source_ref": source})
        return {"ok": True}

    def link(self, piece_id, data, actor, after_sales_ids=()):
        kind = data.get("item_type")
        item_id = field(data.get("item_id"), "关联事项编号", 160)
        if kind not in {"production", "after_sales"}:
            raise ValueError("关联类型无效")
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            require_piece_edit(conn, actor, piece_id, self.store.workspace, associations=True)
            if not conn.execute("SELECT 1 FROM production_pieces WHERE id=?", (piece_id,)).fetchone():
                raise ValueError("单件产品不存在")
            if kind == "production":
                if not conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
                    raise ValueError("生产事项不存在")
            elif item_id not in after_sales_ids:
                raise ValueError("售后事项不存在")
            try:
                conn.execute("INSERT INTO production_links VALUES(?,?,?,?,?)", (kind, item_id, piece_id, now(), actor))
            except sqlite3.IntegrityError:
                raise ValueError("该事项已关联其它单件，请先人工核对") from None
            self._audit(conn, actor, "link_production_record", piece_id, {"type": kind, "item_id": item_id})
        return {"ok": True}

    def unlink(self, piece_id, data, actor):
        kind = data.get("item_type")
        item_id = field(data.get("item_id"), "关联事项编号", 160)
        if kind not in {"production", "after_sales"}:
            raise ValueError("关联类型无效")
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            require_piece_edit(conn, actor, piece_id, self.store.workspace, associations=True)
            row = conn.execute("SELECT piece_id FROM production_links WHERE item_type=? AND item_id=?", (kind, item_id)).fetchone()
            if not row or row["piece_id"] != piece_id:
                raise ValueError("此单件没有对应关联")
            conn.execute("DELETE FROM production_links WHERE item_type=? AND item_id=? AND piece_id=?", (kind, item_id, piece_id))
            self._audit(conn, actor, "unlink_production_record", piece_id, {"type": kind, "item_id": item_id})
        return {"ok": True}

    @staticmethod
    def _audit(conn, actor, action, item_id, detail):
        conn.execute("INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)",
                     (now(), actor, action, item_id, json.dumps(detail, ensure_ascii=False)))
