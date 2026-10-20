"""固定群生产单建档；仅合成XLSX和临时数据库。"""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

from app.production_order_intake import ProductionOrderIntake, parse_order
from app.production import Production
from app.store import Store

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def workbook(number="DY-2026-10-TEST-001", products=None, extra=None):
    rows = [
        ["测试公司生产任务单"], ["生产单编号（订单编号）：" + number],
        ["买方信息：测试客户", "业务员：测试业务", "跟单员：", "电压：220V"],
        ["启动方式：\n", "时间紧任务重；极寒天气", "备注：", "按真实原单制作"],
        ["序号", "产品名称", "产品数量", "单位名称", "规格尺寸\n长/宽/高", "恐龙动态功能配置", "核心工艺参数", "材质要求"],
    ]
    rows += products if products is not None else [["1", "测试蘑菇", "5", "个", "直径1.2米\n高1.5米\n2个\n-----\n直径0.9米\n高1.2米\n3个", "点头", "单独底座", "海绵硅胶"], ["2", "测试树", "1", "组", "高5米", "树枝摆动", "底座", "硅胶"]]
    rows.append(["", "总数：", "6"])
    root = ET.Element("{" + S + "}worksheet")
    sheet_data = ET.SubElement(root, "{" + S + "}sheetData")
    for ri, values in enumerate(rows, 1):
        row = ET.SubElement(sheet_data, "{" + S + "}row", r=str(ri))
        for ci, value in enumerate(values):
            address = chr(65 + ci) + str(ri)
            cell = ET.SubElement(row, "{" + S + "}c", r=address)
            if isinstance(value, tuple):
                ET.SubElement(cell, "{" + S + "}f").text = value[0]
                ET.SubElement(cell, "{" + S + "}v").text = value[1]
            else:
                cell.set("t", "inlineStr")
                ET.SubElement(ET.SubElement(cell, "{" + S + "}is"), "{" + S + "}t").text = str(value)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(root, encoding="utf-8"))
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return stream.getvalue()


class OrderIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.intake = ProductionOrderIntake(self.store)
        self.file = self.root / "生产单DY-2026-10-TEST-001.xlsx"
        self.file.write_bytes(workbook())

    def test_fields_sources_mixed_units_and_explicit_specification(self):
        result = self.intake.import_file(self.file, "production_01|message:1")
        self.assertTrue(result["created"])
        self.assertEqual(result["pieces_created"], 6)
        detail = self.intake.details(result["order_id"])["latest"]
        self.assertEqual(detail["fields"]["followup"], "")
        self.assertEqual(detail["fields"]["startup"], "")
        self.assertEqual(detail["fields"]["business_contact"], "测试业务")
        self.assertEqual(detail["field_sources"]["voltage"], "Sheet1!D3")
        self.assertEqual(detail["totals_by_unit"], {"个": 5, "组": 1})
        self.assertEqual(detail["products"][0]["piece_specifications"], ["直径1.2米\n高1.5米"] * 2 + ["直径0.9米\n高1.2米"] * 3)
        pieces = Production(self.store).snapshot()["orders"][0]["pieces"]
        self.assertTrue(all(s["state"] == "未核对" for p in pieces for s in p["stages"]))
        self.assertTrue(all(p["delivery_state"] == "未安排" for p in pieces))
        self.assertEqual(sum(p["piece_label"].endswith("组") for p in pieces), 1)
        self.file.unlink()
        self.assertEqual(hashlib.sha256(self.intake.source_path(result["document_id"]).read_bytes()).hexdigest(), detail["sha256"])

    def test_duplicate_hash_does_not_create_documents_or_pieces(self):
        first = self.intake.import_file(self.file, "production_01|message:1")
        other = self.root / "重新发送生产单.xlsx"
        other.write_bytes(self.file.read_bytes())
        second = self.intake.import_file(other, "production_01|message:2")
        self.assertEqual(first["document_id"], second["document_id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["pieces_created"], 0)
        self.assertEqual(len(self.intake.details(first["order_id"])["documents"]), 1)

    def test_revision_preserves_manual_progress_and_does_not_duplicate(self):
        first = self.intake.import_file(self.file)
        piece = Production(self.store).snapshot()["orders"][0]["pieces"][0]
        progress = Production(self.store)
        progress.update_stage(piece["id"], {"stage": "机械图", "state": "合格", "basis": "厂内检查", "note": "合成测试保留人工状态"}, "liruiqi")
        progress.update_delivery(piece["id"], {"state": "已交付", "note": "合成出货", "source_ref": "test:1"}, "liruiqi")
        self.file.write_bytes(workbook(products=[["1", "新版测试蘑菇", "8", "个", "高1米", "摇头", "", ""]]))
        second = self.intake.import_file(self.file)
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertFalse(second["created"])
        self.assertEqual(second["pieces_created"], 0)
        current = progress.snapshot()["orders"][0]["pieces"]
        self.assertEqual(len(current), 6)
        preserved = next(p for p in current if p["id"] == piece["id"])
        self.assertEqual(preserved["stages"][0]["state"], "合格")
        self.assertEqual(preserved["delivery_state"], "已交付")
        self.assertEqual(len(self.intake.details(first["order_id"])["documents"]), 2)
        self.assertIn("existing_piece_count_difference", {c["code"] for c in second["checks"]})

    def test_invalid_quantities_are_blank_and_formula_cache_not_used(self):
        products = [[str(i), "测试产品" + str(i), qty, "个", "高1米", "", "", ""] for i, qty in enumerate(["", "0", "-1", "1.5", ("1+2", "3"), "10001"], 1)]
        detail = parse_order("生产单.xlsx", workbook(products=products))
        self.assertTrue(all(p["quantity"] is None for p in detail["products"]))
        self.assertTrue(all(p["piece_specifications"] == [] for p in detail["products"]))
        self.assertEqual(sum(c["code"] == "invalid_quantity" for c in detail["checks"]), 6)

    def test_conflicting_specification_uses_original_quantity_and_blanks(self):
        products = [["1", "瘦长蘑菇", "5", "个", "直径1.1米\n高1.6米\n1个\n-----\n直径0.9米\n高1.4米\n1个\n-----\n直径0.8米\n高1.2米\n1个", "蘑菇PDF的5-6页。", "蘑菇PDF的3-4页。", "硅胶"]]
        detail = parse_order("生产单DY-2026-10-TEST-01.xlsx", workbook(products=products))
        self.assertEqual(detail["products"][0]["quantity"], 5)
        self.assertEqual(detail["products"][0]["piece_specifications"], [""] * 5)
        self.assertTrue({"spec_quantity_conflict", "attachment_page_conflict", "filename_order_number_conflict"} <= {c["code"] for c in detail["checks"]})

    def test_declared_total_conflict_is_separate_from_unit_totals(self):
        data = workbook(products=[["1", "产品甲", "4", "个", "1米", "", "", ""], ["2", "产品乙", "1", "组", "2米", "", "", ""]])
        detail = parse_order("测试生产单.xlsx", data)
        self.assertEqual(detail["totals_by_unit"], {"个": 4, "组": 1})
        self.assertEqual(detail["declared_total"]["value"], 6)
        self.assertEqual(detail["declared_total"]["source_ref"], "Sheet1!C8")
        conflict = next(c for c in detail["checks"] if c["code"] == "total_quantity_conflict")
        self.assertIn("不表示实物总件数", conflict["message"])

    def test_legacy_label_headers_and_common_total_stop_product_rows(self):
        data = workbook()
        source = io.BytesIO(data)
        output = io.BytesIO()
        with zipfile.ZipFile(source) as archive, zipfile.ZipFile(output, "w") as destination:
            for member in archive.infolist():
                value = archive.read(member.filename)
                if member.filename.endswith("sheet1.xml"):
                    value = value.replace("产品名称".encode(), "产品名字".encode()).replace("规格尺寸\n长/宽/高".encode(), "曲线长度".encode()).replace("总数：".encode(), "共计：".encode())
                destination.writestr(member.filename, value)
        detail = parse_order("生产单.xlsx", output.getvalue())
        self.assertEqual(len(detail["products"]), 2)
        self.assertEqual(detail["declared_total"]["value"], 6)
        self.assertEqual(detail["products"][0]["piece_specifications"][0], "直径1.2米\n高1.5米")

    def test_leading_zero_difference_is_not_merged(self):
        first = self.intake.import_file(self.file)
        self.file.write_bytes(workbook(number="DY-2026-10-TEST-01"))
        second = self.intake.import_file(self.file)
        self.assertNotEqual(first["order_id"], second["order_id"])
        self.assertTrue(second["created"])
        self.assertIn("similar_order_number", {c["code"] for c in second["checks"]})

    def test_existing_manual_order_and_piece_are_preserved(self):
        progress = Production(self.store)
        order = progress.add_order({"title": "已有人工单", "order_no": "DY-2026-10-TEST-001", "business_contact": "人工业务员"}, "liruiqi")["id"]
        piece = progress.add_piece({"order_id": order, "piece_label": "原1号", "product_name": "人工产品名"}, "liruiqi")["id"]
        imported = self.intake.import_file(self.file)
        self.assertEqual(imported["order_id"], order)
        snapshot = progress.snapshot()["orders"][0]
        self.assertEqual(snapshot["business_contact"], "人工业务员")
        self.assertEqual(snapshot["pieces"][0]["id"], piece)
        self.assertEqual(snapshot["pieces"][0]["product_name"], "人工产品名")
        self.assertEqual(imported["pieces_created"], 0)

    def test_legacy_prefix_case_insensitive_match_preserves_original_number(self):
        progress = Production(self.store)
        order = progress.add_order({"title": "旧恐龙蛋生产单", "order_no": "DYNM-2026-10-zx-001"}, "liruiqi")["id"]
        self.file.write_bytes(workbook(number="DYNM-2026-10-ZX-001"))
        result = self.intake.import_file(self.file)
        self.assertEqual(result["order_id"], order)
        self.assertFalse(result["created"])
        self.assertEqual(progress.snapshot()["orders"][0]["order_no"], "DYNM-2026-10-zx-001")
        for prefix in ("JNWM", "YYWM", "DYNM"):
            parsed = parse_order("生产单.xlsx", workbook(number=prefix + "-2026-10-zx-002"))
            self.assertEqual(parsed["fields"]["order_no"], prefix + "-2026-10-zx-002")

    def test_unique_legacy_original_filename_links_without_similar_name_guess(self):
        progress = Production(self.store)
        original = "恐龙蛋生产单(3).xlsx"
        order = progress.add_order({"title": "旧蛋生产单", "source_ref": "原生产单：F:\\业务资料\\" + original}, "liruiqi")["id"]
        piece = progress.add_piece({"order_id": order, "piece_label": "原蛋1号", "product_name": "原恐龙蛋"}, "liruiqi")["id"]
        exported = self.root / ("1792030614000_308_" + original)
        exported.write_bytes(workbook(number="DYNM-2026-10-zx-002"))
        result = self.intake.import_file(exported, "production_01|message/test.db:308")
        self.assertEqual(result["order_id"], order)
        self.assertEqual(result["pieces_created"], 0)
        self.assertEqual(self.intake.details(order)["latest"]["filename"], original)
        self.assertEqual(progress.snapshot()["orders"][0]["pieces"][0]["id"], piece)
        unrelated = self.root / "相似恐龙蛋生产单(3).xlsx"
        unrelated.write_bytes(workbook(number="DYNM-2026-10-zx-003"))
        separate = self.intake.import_file(unrelated)
        self.assertNotEqual(separate["order_id"], order)
        self.assertTrue(separate["created"])

    def manifest(self, media_path="media/files/测试生产单.xlsx", valid_file=True):
        folder = self.store.private / "jobs" / "test" / "production" / "production_01" / "生产资料群"
        (folder / "media" / "files").mkdir(parents=True)
        if valid_file:
            (folder / "media" / "files" / "测试生产单.xlsx").write_bytes(workbook())
        data = {"chat_name": "生产资料群", "messages": [{"local_id": 1, "source_db": "message/test.db", "time": "2026-10-20 12:00:00", "sender": "测试群发人", "media": {"kind": "file", "path": media_path, "name": "测试生产单.xlsx", "available": True}}]}
        source = folder / "chat_full_parsed.json"
        source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"status": "exported", "sensitive_cache_cleanup": True, "export_results": [{"source_id": "production_01", "group": "生产资料群", "binding_verified": True, "status": "exported", "source_json": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}

    def test_manifest_only_explicit_whitelist_attachment_and_deduplicates(self):
        manifest = self.manifest()
        manifest["export_results"].append({"source_id": "production_02", "group": "其他群", "binding_verified": True, "source_json": "C:/not-readable.json"})
        first = self.intake.ingest_manifest(manifest)
        second = self.intake.ingest_manifest(manifest)
        self.assertEqual(first["created"], 1)
        self.assertEqual(first["imported"], 1)
        self.assertEqual(first["errors"], [])
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(second["pieces_created"], 0)
        detail = self.intake.details(first["results"][0]["order_id"])["latest"]
        self.assertEqual(detail["fields"]["followup"], "")
        self.assertEqual(detail["fields"]["business_contact"], "测试业务")

    def test_attachment_traversal_is_recorded_and_not_opened(self):
        manifest = self.manifest("../../../../生产单.xlsx")
        result = self.intake.ingest_manifest(manifest)
        self.assertEqual(result["created"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("附件路径", result["errors"][0]["message"])
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM production_order_intake_events WHERE status='failed'").fetchone()[0], 1)

    def test_source_hash_mismatch_does_not_import(self):
        manifest = self.manifest()
        manifest["export_results"][0]["source_sha256"] = "0" * 64
        result = self.intake.ingest_manifest(manifest)
        self.assertEqual(result["created"], 0)
        self.assertIn("摘要不一致", result["errors"][0]["message"])

    def test_wrong_group_and_macro_are_rejected(self):
        manifest = self.manifest()
        manifest["export_results"][0]["group"] = "私人聊天"
        with self.assertRaisesRegex(ValueError, "白名单"):
            self.intake.ingest_manifest(manifest)
        self.file.write_bytes(workbook(extra={"xl/vbaProject.bin": b"not-executed"}))
        with self.assertRaisesRegex(ValueError, "包含宏"):
            self.intake.import_file(self.file)


if __name__ == "__main__":
    unittest.main()
