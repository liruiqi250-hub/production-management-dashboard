import tempfile
import unittest

from app.production import Production
from app.store import Store


class ProductionProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.progress = Production(self.store)
        self.order = self.progress.add_order({"title": "测试生产单", "order_no": "T-001", "business_contact": "业务甲", "received_on": "2026-10-14"}, "liruiqi")["id"]
        self.piece_a = self.progress.add_piece({"order_id": self.order, "piece_label": "1号", "product_name": "测试恐龙"}, "liruiqi")["id"]
        self.piece_b = self.progress.add_piece({"order_id": self.order, "piece_label": "2号", "product_name": "测试恐龙"}, "liruiqi")["id"]
        self.store.create_user('zhangying', '张英', 'editor')
        item = self.store.add_item({'title':'测试本人跟进事项','owner':'张英'},'liruiqi')
        self.progress.link(self.piece_a,{'item_type':'production','item_id':item['id']},'liruiqi')

    def test_business_agreement_affects_only_named_piece_and_stage(self):
        with self.assertRaisesRegex(ValueError, "消息来源"):
            self.progress.update_stage(self.piece_a, {"stage": "海绵（造型）", "state": "合格", "basis": "业务口头放行", "note": "1号造型可以推进", "source_ref": ""}, "zhangying")
        self.progress.update_stage(self.piece_a, {"stage": "海绵（造型）", "state": "合格", "basis": "业务口头放行", "note": "业务明确同意1号造型进入下一步", "source_ref": "测试群 2026-10-14 10:30"}, "zhangying")
        snapshot = self.progress.snapshot()["orders"][0]
        first = next(piece for piece in snapshot["pieces"] if piece["id"] == self.piece_a)
        second = next(piece for piece in snapshot["pieces"] if piece["id"] == self.piece_b)
        self.assertEqual(first["stages"][2]["state"], "合格")
        self.assertEqual(first["stages"][3]["state"], "未核对")
        self.assertEqual(first["delivery_state"], "未安排")
        self.assertTrue(all(stage["state"] == "未核对" for stage in second["stages"]))
        reopened = next(piece for piece in Production(self.store).snapshot()["orders"][0]["pieces"] if piece["id"] == self.piece_a)
        self.assertEqual(reopened["stages"][2]["source_ref"], "测试群 2026-10-14 10:30")

    def test_rework_pause_and_exemption_keep_history(self):
        self.progress.update_stage(self.piece_a, {"stage": "上色（喷色）", "state": "返工", "basis": "现场进展", "note": "喷色脱落，重新喷色", "source_ref": "现场反馈"}, "zhangying")
        self.progress.update_stage(self.piece_a, {"stage": "上色（喷色）", "state": "暂缓", "basis": "人工核对", "note": "等待业务确认颜色", "source_ref": ""}, "zhangying")
        self.progress.update_stage(self.piece_a, {"stage": "刷胶", "state": "不适用", "basis": "人工核对", "note": "该皮套工艺不刷胶", "source_ref": ""}, "zhangying")
        piece = next(piece for piece in self.progress.snapshot()["orders"][0]["pieces"] if piece["id"] == self.piece_a)
        self.assertEqual(piece["stages"][4]["state"], "不适用")
        self.assertEqual(piece["stages"][5]["state"], "暂缓")
        self.assertEqual([u["new_value"] for u in reversed(piece["updates"])], ["返工", "暂缓", "不适用"])
        with self.assertRaisesRegex(ValueError, "实际依据"):
            self.progress.update_delivery(self.piece_a, {"state": "已交付", "note": "", "source_ref": ""}, "zhangying")

    def test_duplicate_piece_and_wrong_association_rejected(self):
        with self.assertRaisesRegex(ValueError, "单件编号已存在"):
            self.progress.add_piece({"order_id": self.order, "piece_label": "1号", "product_name": "另一件"}, "liruiqi")
        with self.assertRaisesRegex(ValueError, "生产事项不存在"):
            self.progress.link(self.piece_a, {"item_type": "production", "item_id": "invented"}, "liruiqi")
        with self.assertRaisesRegex(ValueError, "售后事项不存在"):
            self.progress.link(self.piece_a, {"item_type": "after_sales", "item_id": "invented"}, "liruiqi", set())

    def test_master_correction_and_link_reversal(self):
        self.progress.update_order(self.order, {"order_no": "T-001-CORRECTED", "source_ref": "已核对正式生产单"}, "liruiqi")
        self.progress.update_piece(self.piece_a, {"product_name": "修正后的产品名称"}, "liruiqi")
        with self.assertRaisesRegex(ValueError, "生产单号已存在"):
            another = self.progress.add_order({"title": "另一生产单"}, "liruiqi")["id"]
            self.progress.update_order(another, {"order_no": "T-001-CORRECTED"}, "liruiqi")
        item = self.store.add_item({"title": "样本事项", "category": "任务", "summary": "核对进度"}, "liruiqi")
        self.progress.link(self.piece_a, {"item_type": "production", "item_id": item["id"]}, "liruiqi")
        with self.assertRaisesRegex(ValueError, "已关联其它单件"):
            self.progress.link(self.piece_b, {"item_type": "production", "item_id": item["id"]}, "liruiqi")
        self.progress.unlink(self.piece_a, {"item_type": "production", "item_id": item["id"]}, "liruiqi")
        self.progress.link(self.piece_b, {"item_type": "production", "item_id": item["id"]}, "liruiqi")
        pieces = {piece["id"]: piece for piece in self.progress.snapshot()["orders"][-1]["pieces"]}
        self.assertEqual(pieces[self.piece_a]["product_name"], "修正后的产品名称")
        self.assertEqual(pieces[self.piece_b]["links"][0]["item_id"], item["id"])


if __name__ == "__main__":
    unittest.main()
