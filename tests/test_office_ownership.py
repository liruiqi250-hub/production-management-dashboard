"""办公室范围回归，消息均为测试虚构内容。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from app.office import office_policy, canonical_name
from app.ownership import verify_owner
from app.jobs import candidate_schema
from app.store import Store
from app.after_sales import AfterSales
from tests.test_store_server import synthetic_snapshot
from tests.test_unified_dashboard import ledger


class OfficeOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.policy=office_policy(self.root);self.store=Store(self.root)

    def candidate(self,owner='张英',sender='大洋艺术张英',kind='inferred'):
        item=synthetic_snapshot()['items'][0];item['owner']=owner
        item['evidence'][0].update(sender=sender,text='这件事我继续协调，请补充实际处理结果。')
        item['owner_basis']={'kind':kind,'reason':'虚构跟进依据','locators':[item['evidence'][0]['locator']]}
        return item

    def test_external_proactive_sender_cannot_be_owner(self):
        item=self.candidate(owner='虚构业务员',sender='虚构业务员')
        verify_owner(item,office=self.policy)
        self.assertIsNone(item['owner']);self.assertEqual(item['owner_basis']['kind'],'unassigned')
        self.assertIn('办公室',item['owner_basis']['reason'])

    def test_exact_alias_normalizes_office_name_and_keeps_original_sender(self):
        item=self.candidate();verify_owner(item,office=self.policy)
        self.assertEqual(item['owner'],'张英')
        self.assertEqual(item['owner_basis']['evidence'][0]['sender'],'大洋艺术张英')
        self.assertIsNone(canonical_name('张英的客户',self.policy))
        self.assertIsNone(canonical_name('我',self.policy))

    def test_external_assignment_is_not_office_owner(self):
        item=self.candidate(owner='虚构安装人员',sender='大洋艺术张英',kind='assigned')
        item['evidence'][0]['text']='请虚构安装人员到现场检查'
        verify_owner(item,office=self.policy)
        self.assertIsNone(item['owner'])

    def test_sales_request_does_not_prove_office_acceptance(self):
        item=self.candidate(sender='虚构业务员')
        with self.assertRaisesRegex(ValueError,'真实发言人'):verify_owner(item,office=self.policy)

    def test_office_explicit_assignment_can_be_from_external_sender(self):
        item=self.candidate(sender='虚构业务员',kind='assigned')
        item['evidence'][0]['text']='@大洋艺术张英 请跟进这份资料的修改结果'
        verify_owner(item,office=self.policy)
        self.assertEqual(item['owner'],'张英')

    def test_schema_uses_enum_for_both_scopes(self):
        props=candidate_schema(self.policy)['properties']
        for key in ['items','after_sales_cases']:
            self.assertEqual(props[key]['items']['properties']['owner']['enum'],self.policy['names']+[None])

    def test_new_office_candidate_replaces_old_external_assignment(self):
        data=synthetic_snapshot();data['items']=[self.candidate(owner='虚构业务员',sender='虚构业务员',kind='assigned')]
        # 模拟V0.2.10遗留数据库，不能经新导入提前清洗后才测试。
        with self.store.connect() as c:c.execute('INSERT INTO items(id,base_json) VALUES(?,?)',(data['items'][0]['id'],json.dumps(data['items'][0])))
        data['items']=[self.candidate()];verify_owner(data['items'][0],office=self.policy)
        self.store.import_snapshot(data)
        self.assertEqual(self.store.snapshot()['items'][0]['owner'],'张英')

    def test_legacy_snapshot_filters_external_owner_without_touching_database(self):
        item=self.candidate(owner='虚构业务员',sender='虚构业务员')
        raw=json.dumps(item)
        with self.store.connect() as c:c.execute('INSERT INTO items(id,base_json) VALUES(?,?)',(item['id'],raw))
        visible=self.store.snapshot()['items'][0]
        self.assertIsNone(visible['owner']);self.assertIn('虚构业务员',visible['communication_participants'])
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT base_json FROM items').fetchone()[0],raw)

    def test_manual_selection_rejects_external_owner_and_preserves_state(self):
        data=synthetic_snapshot();self.store.import_snapshot(data)
        with self.assertRaisesRegex(ValueError,'办公室'):self.store.update_item('SYNTHETIC-001',{'owner':'虚构业务员'},'liruiqi')
        self.assertIsNone(self.store.snapshot()['items'][0]['owner'])
        item=self.store.update_item('SYNTHETIC-001',{'owner':'张英'},'liruiqi')
        self.assertEqual(item['owner_basis']['kind'],'manual')

    def test_sales_scope_is_the_same(self):
        sales=AfterSales(self.store);data=ledger();data['cases'][0]['owner']='虚构业务员'
        sales.import_ledger(data,'0'*64);item=sales.snapshot()['cases'][0]
        self.assertIsNone(item['owner'])
        actor={'role':'admin','username':'liruiqi','display_name':'李睿琪'}
        with self.assertRaisesRegex(ValueError,'办公室'):sales.update(item['id'],{'owner':'虚构业务员'},actor)
        self.assertEqual(sales.update(item['id'],{'owner':'郭逸'},actor)['owner'],'郭逸')

    def test_custom_private_alias_requires_target_in_office(self):
        path=self.root/'private/office_staff.json'
        path.write_text(json.dumps({'names':['张英'],'aliases':{'虚构内部昵称':'张英'}}),encoding='utf-8')
        self.assertEqual(canonical_name('虚构内部昵称',office_policy(self.root)),'张英')
        path.write_text(json.dumps({'names':['张英'],'aliases':{'虚构昵称':'虚构业务员'}}),encoding='utf-8')
        with self.assertRaises(ValueError):office_policy(self.root)

    def test_legacy_manual_outsider_is_retained_in_storage_when_saving_status(self):
        data=synthetic_snapshot();self.store.import_snapshot(data)
        with self.store.connect() as c:c.execute('UPDATE items SET override_json=?',(json.dumps({'owner':'虚构旧人工姓名'}),))
        shown=self.store.snapshot()['items'][0]
        self.assertIsNone(shown['owner']);self.assertEqual(shown['previous_manual_owner'],'虚构旧人工姓名')
        self.store.update_item(shown['id'],{'status':'处理中'},'liruiqi')
        with self.store.connect() as c:self.assertEqual(json.loads(c.execute('SELECT override_json FROM items').fetchone()[0])['owner'],'虚构旧人工姓名')


if __name__=='__main__':unittest.main()
