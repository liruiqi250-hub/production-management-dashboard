"""虚构消息：来源判断、人工确认、覆盖保护及历史导出。"""
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from app.after_sales import AfterSales
from app.jobs import candidate_schema, verify_candidates
from app.ownership import verify_owner, owner_description
from app.reports import build_report, render_report_csv, render_report_html, render_report_brief_html
from app.store import Store
from tests.test_store_server import synthetic_snapshot
from tests.test_unified_dashboard import ledger


def proposed():
    item = synthetic_snapshot()['items'][0]
    item['owner'] = item['evidence'][0]['sender']
    item['owner_basis'] = {'kind': 'inferred', 'reason': '虚构主动跟进消息', 'locators': [item['evidence'][0]['locator']]}
    return item


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.snapshot = synthetic_snapshot()
        self.snapshot['items'] = [proposed()]
        verify_owner(self.snapshot['items'][0])
        self.store.import_snapshot(self.snapshot)
        self.sales = AfterSales(self.store)
        self.actor = {'username': 'liruiqi', 'display_name': '李睿琪', 'role': 'admin'}

    def test_real_sender_required_and_locators_cannot_be_invented(self):
        for mutation in [{'owner': '另一个虚构人'}, {'owner_basis': {'kind': 'inferred', 'reason': '虚构理由', 'locators': ['不存在']}}]:
            item = proposed(); item.update(mutation)
            with self.assertRaises(ValueError): verify_owner(item)

    def test_assign_requires_name_in_message_and_ai_cannot_claim_manual(self):
        item = proposed(); item['owner_basis']['kind'] = 'assigned'
        with self.assertRaises(ValueError): verify_owner(item)
        item['evidence'][0]['text'] = '交给虚构测试人员跟进此事'
        verify_owner(item)
        self.assertIn('原文明示分派', owner_description(item))
        item['owner_basis']['kind'] = 'manual'
        with self.assertRaises(ValueError): verify_owner(item)

    def test_acknowledgment_does_not_identify_owner(self):
        for text in ['收到', '好的。', 'OK', '[视频]', '安排']:
            item = proposed(); item['evidence'][0]['text'] = text
            with self.assertRaises(ValueError): verify_owner(item)

    def test_ambiguity_keeps_real_message_but_no_owner(self):
        item = proposed();item['owner'] = None;item['owner_basis']['kind'] = 'unassigned'
        item['owner_basis']['reason'] = '虚构两人同时推动，待确认'
        verify_owner(item)
        self.assertIsNone(item['owner'])
        self.assertEqual(len(item['owner_basis']['evidence']), 1)

    def test_same_owner_save_does_not_confirm_or_freeze_automatic_value(self):
        original = self.store.snapshot()['items'][0]
        changed = self.store.update_item(original['id'], {'owner': original['owner'], 'deadline': '虚构期限'}, 'liruiqi')
        self.assertEqual(changed['owner_basis']['kind'], 'inferred')
        with self.store.connect() as c:
            self.assertNotIn('owner', json.loads(c.execute('SELECT override_json FROM items').fetchone()[0]))
        self.assertEqual(changed['priority']['since'], original['priority']['since'])

    def test_confirmation_preserved_by_reimport_and_does_not_reset_timer(self):
        old = self.store.snapshot()['items'][0]
        item = self.store.update_item(old['id'], {'owner_confirmation': 'confirmed'}, 'liruiqi')
        self.assertEqual(item['owner_basis']['kind'], 'manual')
        self.assertEqual(item['owner_basis']['name'], '李睿琪')
        self.assertEqual(item['owner_basis']['evidence'], old['owner_basis']['evidence'])
        self.assertEqual(item['priority']['since'], old['priority']['since'])
        self.snapshot['items'][0]['owner'] = '新的虚构AI候选'
        self.store.import_snapshot(self.snapshot)
        self.assertEqual(self.store.snapshot()['items'][0]['owner'], old['owner'])
        self.assertEqual(self.store.snapshot()['items'][0]['owner_basis']['kind'], 'manual')

    def test_manual_change_and_clear_are_audited(self):
        item = self.store.update_item('SYNTHETIC-001', {'owner': '虚构负责人乙'}, 'liruiqi')
        self.assertEqual(item['owner_basis']['kind'], 'manual')
        self.assertTrue(item['owner_basis']['confirmed'])
        item = self.store.update_item('SYNTHETIC-001', {'owner': None}, 'liruiqi')
        self.assertIsNone(item['owner'])
        with self.assertRaises(ValueError): self.store.update_item('SYNTHETIC-001', {'owner_confirmation': 'confirmed'}, 'liruiqi')

    def test_after_sales_confirm_and_normal_save_are_distinct(self):
        data = ledger();case = data['cases'][0]
        case['owner'] = '虚构发言人'
        case['evidence'][0].update(sender='虚构发言人', time='2026-09-30T10:00:00+08:00', text='这个问题我再跟进一下')
        case['owner_basis'] = {'kind': 'inferred', 'reason': '主动跟进', 'locators': ['dayang|' + case['evidence'][0]['source_ref']]}
        verify_owner(case)
        self.sales.import_ledger(data, '0' * 64)
        identity = self.sales.snapshot()['cases'][0]['id']
        original = self.sales.snapshot()['cases'][0]
        self.sales.update(identity, {'owner': original['owner'], 'status': original['status']}, self.actor)
        self.assertEqual(self.sales.snapshot()['cases'][0]['owner_basis']['kind'], 'inferred')
        item = self.sales.update(identity, {'owner_confirmation': 'confirmed'}, self.actor)
        self.assertEqual(item['owner_basis']['kind'], 'manual')
        self.assertEqual(item['priority']['since'], original['priority']['since'])
        data['cases'][0]['owner'] = '虚构另一人';self.sales.import_ledger(data, '1' * 64)
        self.assertEqual(self.sales.snapshot()['cases'][0]['owner'], original['owner'])

    def test_legacy_sales_without_raw_message_is_not_guessed(self):
        self.sales.import_ledger(ledger(), '0' * 64)
        item = self.sales.snapshot()['cases'][0]
        self.assertIsNone(item.get('owner'))
        self.assertEqual(item['owner_basis']['kind'], 'unassigned')

    def test_reports_and_csv_keep_basis_without_future_leak(self):
        item = self.snapshot['items'][0]
        item['owner_basis']['checked_at'] = '2026-10-08T10:00:00+08:00'
        report = build_report(self.snapshot, 'weekly', '2026-10-12')
        self.assertIn('消息推定', render_report_html(report))
        self.assertIn('消息推定', render_report_brief_html(report))
        rows = list(csv.reader(io.StringIO(render_report_csv(report))))
        self.assertIn('负责人判断依据', rows[0]);self.assertEqual(len(rows[0]), len(rows[1]))
        item['owner_basis']['checked_at'] = '2026-10-13T10:00:00+08:00'
        historical = build_report(self.snapshot, 'weekly', '2026-10-12')
        self.assertIsNone(historical['items'][0]['owner'])
        self.assertNotIn('虚构测试人员', str(historical['items'][0]['owner_basis']))

    def test_schema_requires_both_scopes_owner_basis(self):
        schema = candidate_schema()['properties']
        for key in ['items', 'after_sales_cases']:
            self.assertIn('owner', schema[key]['items']['required'])
            self.assertIn('owner_basis', schema[key]['items']['required'])

    def test_explicit_assignment_has_priority_over_new_sender(self):
        data = copy.deepcopy(self.snapshot)
        data['items'][0]['owner_basis']['kind'] = 'assigned'
        self.store.import_snapshot(data)
        new = copy.deepcopy(self.snapshot)
        new['items'][0]['owner'] = '另一虚构发言人'
        self.store.import_snapshot(new)
        self.assertEqual(self.store.snapshot()['items'][0]['owner'], data['items'][0]['owner'])
        self.assertEqual(self.store.snapshot()['items'][0]['owner_basis']['kind'], 'assigned')

    def test_verifier_replaces_fabricated_sender_before_owner_check(self):
        item = proposed()
        source = {'messages': [{'source_db': 'message\\message_1.db', 'local_id': 1, 'time': '2026-10-07T10:00:00', 'sender': '真实虚构来源甲', 'content': '这件事我来跟进'}]}
        path = Path(self.temp.name) / 'raw.json';path.write_text(json.dumps(source), encoding='utf-8')
        manifest = {'start_inclusive': self.snapshot['period']['start'], 'end_exclusive': self.snapshot['period']['end'], 'export_results': [
            {**g, **({'source_json': str(path)} if g['source_id'] == 'production_01' else {})} for g in self.snapshot['coverage']['groups']]}
        with self.assertRaisesRegex(ValueError, '真实发言人'):
            verify_candidates(Path(self.temp.name), manifest, {'export_results': []}, {'items': [item], 'after_sales_cases': [], 'limitations': []})


if __name__ == '__main__': unittest.main()
