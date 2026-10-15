"""合成生产阶段资料；不读取真实微信、授权或业务库。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.stage_review import apply_resolutions, load_policy, prepare_resolutions
from app.store import Store
from app.jobs import Jobs, run_job, write_json
from app.office import office_policy
from tests.test_store_server import synthetic_snapshot


POLICY = {"enabled": True, "scope": "production_stage_only", "authorized_by": "liruiqi", "authorized_on": "2026-10-15"}


def example(basis="后续工序已发生"):
    item = synthetic_snapshot()["items"][0]
    one = {"locator": "production_01|message\\message_1.db:1", "time": "2026-10-07T10:00:00+08:00", "sender": "张英", "text": "虚构8号造型确认，可以下一步吗？"}
    text = {"后续工序已发生": "虚构8号纹理", "现场完成反馈": "虚构8号造型调整后", "业务阶段放行": "可以"}[basis]
    two = {"locator": "production_01|message\\message_1.db:2", "time": "2026-10-07T11:00:00+08:00", "sender": "测试业务" if basis == "业务阶段放行" else "张英", "text": text}
    item.update(evidence=[one, two], last_seen=two["time"], closure_scope="仅虚构8号造型阶段", status="待验证")
    item["stage_resolution"] = {"entity": "虚构8号", "stage": "造型", "basis": basis, "locator": two["locator"], "context_locators": [one["locator"]], "remaining_item_ids": [], "reason": "虚构同一产品阶段的上下文依据。"}
    if basis != "后续工序已发生":
        item["stage_confirmation" if basis == "业务阶段放行" else "work_completion"] = {"locator": two["locator"], "time": two["time"], "scope": item["closure_scope"]}
    messages = {one["locator"]: {"type": "文本"}, two["locator"]: {"type": "文本"}}
    return item, messages


class StageReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.office = office_policy(self.root)

    def test_policy_requires_explicit_local_admin_authorization(self):
        self.assertFalse(load_policy(self.root, self.store)["enabled"])
        path = self.root / "private/context_review_policy.json"
        path.write_text(json.dumps(POLICY), encoding="utf-8")
        self.assertTrue(load_policy(self.root, self.store)["enabled"])
        for changes in [{"authorized_by": "zengyue"}, {"scope": "everything"}, {"authorized_on": "invalid"}, {"enabled": "true"}]:
            path.write_text(json.dumps({**POLICY, **changes}), encoding="utf-8")
            with self.assertRaises(ValueError):load_policy(self.root, self.store)

    def test_three_evidence_types_are_limited_to_one_stage(self):
        for basis in ["后续工序已发生", "现场完成反馈", "业务阶段放行"]:
            item, messages = example(basis)
            plans = prepare_resolutions([item], messages, self.office, POLICY)
            self.assertEqual(plans[0]["scope"], "仅虚构8号造型阶段")
            self.assertEqual(plans[0]["source_time"], item["last_seen"])

    def test_plans_future_media_and_cross_group_cannot_close(self):
        item, messages = example()
        for text in ["准备做纹理", "明天喷色", "可以下一步喷色吗", "纹理参考", "还没做完", "收到"]:
            bad = copy.deepcopy(item); bad["evidence"][-1]["text"] = text
            with self.subTest(text=text), self.assertRaises(ValueError):prepare_resolutions([bad], messages, self.office, POLICY)
        for changes in [{"context_locators": []}, {"context_locators": ["production_02|message\\message_1.db:1"]}, {"remaining_item_ids": ["missing"]}]:
            bad = copy.deepcopy(item); bad["stage_resolution"].update(changes)
            with self.assertRaises(ValueError):prepare_resolutions([bad], messages, self.office, POLICY)
        messages[item["evidence"][-1]["locator"]]["type"] = "语音"
        with self.assertRaises(ValueError):prepare_resolutions([item], messages, self.office, POLICY)
        with self.assertRaises(ValueError):prepare_resolutions([item], messages, self.office, {"enabled": False})

    def test_completed_part_can_have_future_followup(self):
        item, messages = example("现场完成反馈")
        item["evidence"][-1]["text"] = "虚构8号造型调整后，明天拍视频"
        remaining = copy.deepcopy(item); remaining["id"] = "SYNTHETIC-REMAINING";remaining["stage_resolution"] = None
        item["stage_resolution"]["remaining_item_ids"] = [remaining["id"]]
        self.assertEqual(len(prepare_resolutions([item, remaining], messages, self.office, POLICY)), 1)
        remaining["stage_resolution"] = copy.deepcopy(item["stage_resolution"])
        with self.assertRaises(ValueError):prepare_resolutions([item, remaining], messages, self.office, POLICY)

    def test_text_reply_card_is_text_but_file_card_is_not(self):
        item, messages = example("业务阶段放行")
        raw = messages[item['evidence'][-1]['locator']]
        raw.update(type='文件/链接/卡片', type_code=(57 << 32 | 49), content='已确认\n    ↳ 引用 张英：虚构8号造型')
        item['evidence'][-1]['text'] = raw['content']
        self.assertEqual(len(prepare_resolutions([item], messages, self.office, POLICY)), 1)
        raw['type_code'] = 49
        with self.assertRaises(ValueError):prepare_resolutions([item], messages, self.office, POLICY)

    def test_automatic_close_audit_idempotence_and_rollback(self):
        item, messages = example();snap = synthetic_snapshot();snap["items"] = [item]
        self.store.import_snapshot(snap)
        plans = prepare_resolutions([item], messages, self.office, POLICY)
        with self.store.connect() as c:result = apply_resolutions(self.store, plans, POLICY, c)
        self.assertEqual(result["closed"], [item["id"]])
        actual = self.store.snapshot()["items"][0]
        self.assertEqual(actual["status"], "已解决")
        self.assertEqual(actual["completed_by_name"], "Codex自动复核")
        self.assertEqual(actual["closed_at"], item["last_seen"])
        with self.store.connect() as c:
            self.assertEqual(apply_resolutions(self.store, plans, POLICY, c)["already_closed"], [item["id"]])
            self.assertEqual(c.execute("SELECT count(*) FROM audit WHERE action='context_stage_completed'").fetchone()[0], 1)
        # 人工重新打开后，自动导入不能覆盖其状态。
        self.store.update_item(item["id"], {"status": "待处理"}, "liruiqi")
        with self.store.connect() as c:self.assertEqual(apply_resolutions(self.store, plans, POLICY, c)["skipped_manual"], [item["id"]])
        self.assertEqual(self.store.snapshot()["items"][0]["status"], "待处理")

    def test_stage_close_rolls_back_with_parent_transaction(self):
        item, messages = example();snap = synthetic_snapshot();snap["items"] = [item];self.store.import_snapshot(snap)
        plans = prepare_resolutions([item], messages, self.office, POLICY)
        with self.assertRaises(RuntimeError):
            with self.store.connect() as c:
                apply_resolutions(self.store, plans, POLICY, c)
                raise RuntimeError("synthetic failure")
        self.assertEqual(self.store.snapshot()["items"][0]["status"], "待验证")

    def test_remaining_work_cannot_point_to_actual_manually_closed_item(self):
        item, messages = example();remaining = copy.deepcopy(item)
        remaining["id"] = "SYNTHETIC-REMAINING"; remaining["stage_resolution"] = None
        item["stage_resolution"]["remaining_item_ids"] = [remaining["id"]]
        snap = synthetic_snapshot();snap["items"] = [item, remaining];self.store.import_snapshot(snap)
        self.store.update_item(remaining["id"], {"status": "已解决", "closure_note": "虚构人工完成说明"}, "liruiqi")
        plans = prepare_resolutions([item, remaining], messages, self.office, POLICY)
        with self.assertRaisesRegex(ValueError, "既有已完成"):
            with self.store.connect() as c:apply_resolutions(self.store, plans, POLICY, c)
        self.assertEqual(self.store.snapshot()["items"][0]["status"], "待验证")

    def test_information_question_answer_can_complete_without_physical_acceptance(self):
        item, messages = example()
        item["evidence"][0]["text"] = "请问遥控启动需不需要恐龙之间连线？"
        item["evidence"][-1].update(sender="测试技术人员", text="遥控启动就不需要")
        item["closure_scope"] = "仅本次遥控连线需求澄清答复"
        item["stage_resolution"].update(stage="资料交付", basis="答复或选择完成")
        self.assertEqual(len(prepare_resolutions([item], messages, self.office, POLICY)), 1)
        item["evidence"][-1]["text"] = "好的，谢谢测试师傅"
        with self.assertRaises(ValueError):prepare_resolutions([item], messages, self.office, POLICY)

    def test_answer_scope_cannot_complete_manufacturing_or_future_reply(self):
        item, messages = example();item["stage_resolution"].update(stage="资料交付", basis="答复或选择完成")
        item["closure_scope"] = "仅本次参数答复"
        item["evidence"][0]["text"] = "请问什么时候能答复？"
        item["evidence"][-1]["text"] = "明天回复你"
        with self.assertRaises(ValueError):prepare_resolutions([item], messages, self.office, POLICY)
        item["evidence"][-1]["text"] = "可以"
        item["stage_resolution"]["stage"] = "钢架"
        with self.assertRaises(ValueError):prepare_resolutions([item], messages, self.office, POLICY)

    def test_excluded_final_acceptance_is_allowed_but_actual_claim_is_not(self):
        item, messages = example()
        item['closure_scope'] = '仅虚构8号造型阶段，不代表客户最终验收或整单完工。'
        self.assertEqual(len(prepare_resolutions([item], messages, self.office, POLICY)), 1)
        item['closure_scope'] = '虚构8号整单完工，客户验收。'
        with self.assertRaises(ValueError):prepare_resolutions([item], messages, self.office, POLICY)

    def test_daily_worker_closes_stage_and_reports_from_real_state(self):
        from app.normalize import SOURCE_GROUPS
        item, messages = example("现场完成反馈");snap = synthetic_snapshot()
        item["work_completion"]["time"] = "2000-01-01T00:00:00+08:00"
        item.update(progress_at="2000-01-01T00:00:00+08:00", progress_locator=item["evidence"][-1]["locator"], progress_note="虚构调整完成")
        job_id = "3" * 32;jobs = Jobs(self.root)
        write_json(self.root / "private/context_review_policy.json", POLICY)
        write_json(jobs.path, {"id": job_id, "state": "queued", "started_at": "2026-10-15T20:00:00+08:00"})
        (jobs.path.parent / job_id).mkdir()
        def process(args, **kwargs):
            if "--scope" in args:
                scope = args[args.index("--scope") + 1];folder = Path(args[args.index("--out") + 1])
                sources = SOURCE_GROUPS if scope == "production" else {"dayang": "虚构售后1", "yunyi": "虚构售后2", "jinnai": "虚构售后3"}
                rows = []
                for key, name in sources.items():
                    row = {"source_id": key, "group": name, "status": "verified_empty_range", "message_count": 0}
                    if key == "production_01":
                        source = folder / "synthetic.json"
                        write_json(source, {"messages": [{"source_db": "message\\message_1.db", "local_id": n, "type": "文本", "time": e["time"], "sender": e["sender"], "content": e["text"]} for n, e in enumerate(item["evidence"], 1)]})
                        import hashlib
                        row.update(status="exported", message_count=2, source_json=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
                    rows.append(row)
                write_json(folder / "collection_manifest_V0.1.3.json", {"status": "exported", "sensitive_cache_cleanup": True, "start_inclusive": snap["period"]["start"], "end_exclusive": snap["period"]["end"], "export_results": rows})
            else:
                payload = json.loads(kwargs["input"].decode().split("\nDATA=", 1)[1])
                self.assertTrue(payload["stage_review_policy"]["enabled"])
                write_json(Path(args[args.index("--output-last-message") + 1]), {"items": [item], "after_sales_cases": [], "limitations": ["合成测试"]})
            return SimpleNamespace(returncode=0)
        with patch("app.jobs.subprocess.run", side_effect=process), patch("app.jobs.codex_executable", return_value="synthetic"), patch("app.jobs.codex_options", return_value=[]):
            self.assertEqual(run_job(self.root, job_id), 0)
        self.assertEqual(jobs.status()["state"], "completed")
        self.assertEqual(jobs.status()["stage_closed"], 1)
        self.assertEqual(self.store.snapshot()["items"][0]["status"], "已解决")
        self.assertEqual(self.store.snapshot()["items"][0]["work_completion"]["time"], "2026-10-07T11:00:00+08:00")
        self.assertEqual(self.store.snapshot()["items"][0]["progress_at"], "2026-10-07T11:00:00+08:00")
        result = json.loads((jobs.path.parent / job_id / "stage_review_result.json").read_text("utf-8"))
        self.assertEqual(result["closed"], [item["id"]])


if __name__ == "__main__":unittest.main()
