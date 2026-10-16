"""全部虚构材料：在真实HTTP服务上验证逐事项写入限制和拒绝后原子性。"""
import copy
import hashlib
import json
import unittest
from tests import test_store_server as fixture
from urllib.parse import quote
from tests.test_unified_dashboard import ledger
from app.permissions import ItemPermissionError


class ItemPermissionsTests(unittest.TestCase):
    request = fixture.StoreServerTests.request
    login = fixture.StoreServerTests.login
    close_server = fixture.StoreServerTests.close_server

    def setUp(self):
        fixture.StoreServerTests.setUp(self)
        self.store.create_user('zhangying','张英','editor')
        self.store.create_user('yinxiaoqin','银小芹','editor')
        with self.store.connect() as c:
            from app.store import password_hash
            for name in ('zhangying','yinxiaoqin'):
                salt=c.execute('SELECT salt FROM users WHERE username=?',(name,)).fetchone()[0]
                c.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE username=?',(password_hash(self.password,salt),name))
        self.own=self.store.add_item({'title':'虚构本人事项','owner':'张英'},'liruiqi')['id']
        self.other=self.store.add_item({'title':'虚构他人事项','owner':'银小芹'},'liruiqi')['id']
        self.unassigned=self.store.add_item({'title':'虚构待分派事项'},'liruiqi')['id']
        data=ledger();data['cases']=[]
        for ident,owner in [('OWN','张英'),('OTHER','银小芹'),('NONE',None)]:
            item=copy.deepcopy(ledger()['cases'][0]);item.update(id=ident,owner=owner);data['cases'].append(item)
        self.server.after_sales.import_ledger(data,'0'*64)
        self.employee=self.login('zhangying');self.boss=self.login('zengyue');self.admin=self.login('liruiqi')

    def hashes(self):
        tables=('users','items','after_sales','production_stages','production_pieces','production_links','production_updates','audit')
        with self.store.connect() as c:
            return {t:hashlib.sha256(json.dumps([tuple(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY rowid')],default=str,ensure_ascii=False).encode()).hexdigest() for t in tables}

    def patch(self,path,changes,actor=None):
        return self.request('PATCH',quote(path,safe='/:'),changes,self.employee if actor is None else actor)

    def test_employee_can_update_and_complete_own_production(self):
        path='/api/items/'+self.own
        self.assertEqual(self.patch(path,{'status':'处理中','deadline':'虚构期限'})[0],200)
        self.assertEqual(self.patch(path,{'owner':'张英','owner_confirmation':'confirmed'})[0],200)
        self.assertEqual(self.patch(path,{'status':'已解决','closure_note':'虚构阶段实际完成','closure_scope':'仅虚构阶段'})[0],200)

    def test_other_and_unassigned_reject_all_writes_without_side_effects(self):
        before=self.hashes()
        for item in (self.other,self.unassigned):
            for changes in ({'status':'处理中'},{'deadline':'越权期限'},{'owner':'张英'},{'owner':'张英','owner_confirmation':'confirmed'},{'status':'已解决','closure_note':'越权完成'}):
                with self.subTest(item=item,changes=changes):self.assertEqual(self.patch('/api/items/'+item,changes)[0],403)
        self.assertEqual(before,self.hashes())

    def test_own_owner_cannot_be_reassigned_or_cleared(self):
        before=self.hashes()
        for owner in ('银小芹',None,''):
            self.assertEqual(self.patch('/api/items/'+self.own,{'owner':owner,'status':'处理中'})[0],403)
        self.assertEqual(before,self.hashes())

    def test_employee_sales_own_allowed_and_others_rejected_atomically(self):
        self.assertEqual(self.patch('/api/after-sales/2026-09:大洋:OWN',{'progress_note':'虚构实际检查结果','status':'待检查'})[0],200)
        before=self.hashes()
        for ident in ('OTHER','NONE'):
            for changes in ({'status':'待检查'},{'owner':'张英'},{'status':'完成验收','progress_note':'越权验收'}):
                self.assertEqual(self.patch('/api/after-sales/2026-09:大洋:'+ident,changes)[0],403)
        self.assertEqual(self.patch('/api/after-sales/2026-09:大洋:OWN',{'owner':'银小芹'})[0],403)
        self.assertEqual(before,self.hashes())

    def test_both_managers_can_process_any_owner_and_unassigned(self):
        for actor in (self.admin,self.boss):
            for item in (self.own,self.other,self.unassigned):self.assertEqual(self.patch('/api/items/'+item,{'status':'处理中'},actor)[0],200)
            for ident in ('OWN','OTHER','NONE'):self.assertEqual(self.patch('/api/after-sales/2026-09:大洋:'+ident,{'status':'待检查'},actor)[0],200)
        self.assertEqual(self.patch('/api/items/'+self.unassigned,{'owner':'张英'},self.boss)[0],200)
        self.assertEqual(self.patch('/api/after-sales/2026-09:大洋:NONE',{'owner':'张英'},self.boss)[0],200)
        self.assertEqual(self.request('POST','/api/users',{'username':'not_allowed','display_name':'虚构','role':'editor'},self.boss)[0],403)

    def test_reassignment_uses_current_override_and_rechecks_each_write(self):
        self.assertEqual(self.patch('/api/items/'+self.own,{'owner':'银小芹'},self.admin)[0],200)
        self.assertEqual(self.patch('/api/items/'+self.own,{'status':'处理中'})[0],403)
        self.assertEqual(self.patch('/api/items/'+self.own,{'owner':'张英','status':'处理中'})[0],403)
        self.assertEqual(self.patch('/api/items/'+self.own,{'status':'处理中'},self.login('yinxiaoqin'))[0],200)

    def test_store_and_sales_direct_calls_enforce_database_actor(self):
        with self.assertRaises(ItemPermissionError):self.store.update_item(self.other,{'owner':'张英'},'zhangying')
        forged={'username':'zhangying','display_name':'李睿琪','role':'admin'}
        with self.assertRaises(ItemPermissionError):self.server.after_sales.update('2026-09:大洋:OTHER',{'status':'待检查'},forged)

    def test_all_records_remain_readable_and_bearer_tokens_remain_read_only(self):
        status,body,_=self.request('GET','/api/dashboard',headers=self.employee)
        self.assertEqual(status,200);self.assertIn(self.other,{i['id'] for i in json.loads(body)['items']})
        _,body,_=self.request('POST','/api/tokens',{'label':'虚构只读测试','days':7},self.boss)
        token=json.loads(body)['token'];self.assertEqual(self.patch('/api/items/'+self.other,{'status':'处理中'},{'Authorization':'Bearer '+token})[0],403)

    def test_read_only_and_unmapped_employee_cannot_write(self):
        viewer=self.login('test_viewer');self.assertEqual(self.patch('/api/items/'+self.own,{'status':'处理中'},viewer)[0],403)
        self.store.create_user('unmapped','张英助理','editor')
        with self.assertRaises(ItemPermissionError):self.store.update_item(self.own,{'status':'处理中'},'unmapped')

    def piece(self,items=()):
        p=self.server.production;order=p.add_order({'title':'虚构权限生产单'},'liruiqi')['id'];piece=p.add_piece({'order_id':order,'piece_label':'虚构1号','product_name':'虚构恐龙'},'liruiqi')['id']
        for item in items:p.link(piece,{'item_type':'production','item_id':item},'liruiqi')
        return piece

    def test_piece_progress_cannot_bypass_item_ownership(self):
        own=self.piece([self.own]);own2=self.store.add_item({'title':'另一虚构本人事项','owner':'张英'},'liruiqi')['id'];mixed=self.piece([own2,self.other]);unlinked=self.piece()
        change={'stage':'机械图','state':'进行中','basis':'现场进展','note':'虚构进展','source_ref':''}
        self.assertEqual(self.patch('/api/production/pieces/'+own+'/stage',change)[0],200)
        before=self.hashes()
        for piece in (mixed,unlinked):
            self.assertEqual(self.patch('/api/production/pieces/'+piece+'/stage',change)[0],403)
            self.assertEqual(self.patch('/api/production/pieces/'+piece+'/delivery',{'state':'待发货','note':''})[0],403)
        self.assertEqual(before,self.hashes())
        for actor in (self.admin,self.boss):self.assertEqual(self.patch('/api/production/pieces/'+mixed+'/stage',change,actor)[0],200)

    def test_associations_cannot_be_changed_to_gain_piece_access(self):
        own=self.piece([self.own]);unlinked=self.piece();before=self.hashes()
        self.assertEqual(self.patch('/api/production/pieces/'+own+'/unlink',{'item_type':'production','item_id':self.own})[0],403)
        self.assertEqual(self.patch('/api/production/pieces/'+unlinked+'/link',{'item_type':'production','item_id':self.unassigned})[0],403)
        self.assertEqual(before,self.hashes())
        self.assertEqual(self.patch('/api/production/pieces/'+unlinked+'/link',{'item_type':'production','item_id':self.unassigned},self.boss)[0],200)

    def test_me_exposes_server_computed_scope(self):
        for actor,manager,name in ((self.employee,False,'张英'),(self.boss,True,None),(self.admin,True,'李睿琪')):
            status,body,_=self.request('GET','/api/me',headers=actor);self.assertEqual(status,200);scope=json.loads(body)['user']['permissions'];self.assertEqual(scope['manage_all'],manager);self.assertEqual(scope['office_name'],name)


if __name__=='__main__':unittest.main()
