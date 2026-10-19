import copy
from datetime import datetime
import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from app.notification_cards import card_body, freeze_report, load_report, check_failure, operator_config, preview
from app.dingtalk_notifications import Notifications, RobotClient, record_leader_route
from app.store import Store, TZ, digest
from app.server import DashboardServer, COOKIE
from tests.test_store_server import synthetic_snapshot
from tests.test_dingtalk_notifications import FakeClient, ORIGIN
from tests.test_dingtalk_login import DingTalkFixture

AT = datetime(2026,10,18,16,50,tzinfo=TZ)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name));self.snapshot=synthetic_snapshot()

    def test_frozen_report_does_not_follow_later_business_changes(self):
        ident=freeze_report(self.store,self.snapshot,'2026-10-12','卡片预览')
        before=load_report(self.store,ident)
        self.snapshot['items']=[]
        self.assertEqual(load_report(self.store,ident),before)
        self.assertEqual(ident,freeze_report(self.store,synthetic_snapshot(),'2026-10-12','卡片预览'))
        for invalid in ['../production_V0.2.0','a'*31,'a'*33,'../'+'a'*32]:
            with self.assertRaises(ValueError):load_report(self.store,invalid)

    def test_card_uses_two_fixed_origin_buttons_and_short_text(self):
        body=card_body('生产日报\n'+('待跟进事项\n'*150),ORIGIN,'a'*32)
        card=json.loads(body)['notification_card'];p=card['msgParam']
        self.assertEqual(card['msgKey'],'sampleActionCard2');self.assertLess(len(p['text']),500)
        self.assertEqual(p['actionURL1'],ORIGIN+'/')
        self.assertEqual(p['actionURL2'],ORIGIN+'/?report='+'a'*32)
        client=RobotClient({},'example-robot')
        with patch.object(client,'_post',return_value={'processQueryKey':'example-receipt'}) as post:
            client.send('operator-preview','example-only-me',body)
        self.assertEqual(post.call_args.args[1]['userIds'],['example-only-me'])
        self.assertEqual(json.loads(post.call_args.args[1]['msgParam']),p)

    def test_frozen_pdf_requires_login_and_keeps_attachment_headers(self):
        ident=freeze_report(self.store,self.snapshot,'2026-10-12','卡片预览')
        server=DashboardServer(('127.0.0.1',0),self.store,'192.168.10.237')
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        def request(headers):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            try:
                c.request('GET','/api/notification-report?id='+ident,headers=headers)
                r=c.getresponse();return r.status,dict(r.getheaders()),r.read()
            finally:c.close()
        try:
            with patch('app.pdf_export.render_report_pdf',return_value=b'%PDF-synthetic') as render:
                self.assertEqual(request({})[0],401);render.assert_not_called()
                made=self.store.create_user('example_reader','合成测试','viewer')
                self.store.change_password('example_reader',made['initial_password'],'synthetic-password')
                token=self.store.login('example_reader','synthetic-password')['token']
                status,headers,body=request({'Cookie':COOKIE+'='+token})
                self.assertEqual(status,200);self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
                self.assertEqual(body,b'%PDF-synthetic');self.assertEqual(headers['Cache-Control'],'no-store')
                self.assertIn('卡片预览',render.call_args.args[0])
        finally:server.shutdown();server.server_close();thread.join(2)


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name));self.client=FakeClient();self.service=Notifications(self.store,client=self.client)
        self.patch=patch('app.notification_cards.operator_config',return_value=({'robot_code':'example-robot'},{},ORIGIN,'example-only-me'))
        self.patch.start();self.addCleanup(self.patch.stop)

    def run_check(self,state,at=AT,deadline=False):return check_failure(self.store,state,at=at,deadline=deadline,service=self.service)

    def test_failure_waits_until_1630_and_shares_deadline_dedupe(self):
        state={'id':'example-job','started_at':AT.replace(hour=15,minute=30).isoformat(),'state':'failed','message':'sensitive-source-not-for-notification'}
        self.run_check(state,at=AT.replace(hour=15,minute=47))
        self.run_check(state,at=AT.replace(hour=16,minute=29))
        self.assertFalse(self.client.calls)
        self.run_check(state,at=AT.replace(hour=16,minute=30));self.run_check(state,deadline=True)
        self.assertEqual(len(self.client.calls),1)
        channel,target,text=self.client.calls[0];self.assertEqual((channel,target),('operator-alert','example-only-me'))
        self.assertIn('整理失败',text);self.assertNotIn('sensitive-source',text)

    def test_deadline_covers_not_started_running_and_old_success(self):
        cases=[{}, {'state':'completed','started_at':'2026-10-17T15:30:00+08:00'},
               {'state':'completed','started_at':'2026-10-18T09:00:00+08:00'},
               {'state':'collecting','started_at':'2026-10-18T15:30:00+08:00'}]
        for n,state in enumerate(cases):
            self.run_check(state,at=AT.replace(day=18+n),deadline=True)
        self.assertEqual(len(self.client.calls),4)

    def test_completed_and_early_check_send_nothing(self):
        self.run_check({},at=AT.replace(hour=16,minute=29),deadline=True)
        self.run_check({'started_at':AT.replace(hour=15,minute=30).isoformat(),'state':'completed'},deadline=True)
        self.run_check({'started_at':AT.replace(hour=9).isoformat(),'state':'failed'},at=AT.replace(hour=10))
        self.assertFalse(self.client.calls)

    def test_failure_recovered_before_check_never_sends_warning(self):
        state={'id':'example-job','started_at':AT.replace(hour=15,minute=35).isoformat(),'state':'failed'}
        self.run_check(state,at=AT.replace(hour=15,minute=47))
        state['state']='completed'
        self.run_check(state,at=AT.replace(hour=16,minute=30),deadline=True)
        self.run_check(state,deadline=True)
        self.assertFalse(self.client.calls)

    def test_first_deadline_check_at_1630_then_1650_sends_once(self):
        self.run_check({},at=AT.replace(hour=16,minute=30),deadline=True)
        self.run_check({},at=AT.replace(hour=16,minute=50),deadline=True)
        self.assertEqual(len(self.client.calls),1)

    def test_unknown_alert_is_not_automatically_repeated_and_late_label(self):
        self.client.error=TimeoutError()
        self.run_check({},at=AT.replace(hour=17),deadline=True);self.client.error=None
        self.run_check({},at=AT.replace(hour=17,minute=1),deadline=True)
        self.assertEqual(len(self.client.calls),1);self.assertTrue(self.client.calls[0][2].startswith('【延迟补发】'))

    def test_preview_only_sends_two_cards_to_self_with_source_date(self):
        snapshot=synthetic_snapshot();snapshot['imported_at']='2026-10-12T16:00:00+08:00'
        with patch.object(self.store,'snapshot',return_value=snapshot),patch('app.after_sales.AfterSales.snapshot',return_value={'cases':[]}):
            preview(self.store,at=AT,service=self.service);preview(self.store,at=AT,service=self.service)
        self.assertEqual(len(self.client.calls),2)
        for channel,target,body in self.client.calls:
            self.assertEqual((channel,target),('operator-preview','example-only-me'))
            self.assertIn('资料截至 2026-10-12',body);self.assertNotIn('今日新增生产事项',body)


class OperatorIdentityTests(DingTalkFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        with self.store.connect() as c:c.execute('INSERT INTO dingtalk_identities VALUES(?,?,?,?)',(self.config['corp_id'],digest('example-me'),'liruiqi','example-time'))
        remote=self.store.private/'remote_access';remote.mkdir()
        (remote/(self.store.path.stem+'.json')).write_text(json.dumps({'enabled':True,'public_origin':ORIGIN,'gateway_secret':'example-'+('x'*43)}),'utf-8')
        self.path=self.store.private/'dingtalk/notifications.json'
        self.path.write_text(json.dumps({'capture_operator_route':True,'operator_alerts_enabled':True,'robot_code':self.config['client_id']}),'utf-8')

    def test_only_original_active_operator_binding_can_receive(self):
        record_leader_route(self.store,self.config['corp_id'],'example-me','example-me-userid')
        self.assertEqual(operator_config(self.store)[3],'example-me-userid')
        record_leader_route(self.store,'wrong-corp','example-me','wrong-userid')
        self.assertEqual(operator_config(self.store)[3],'example-me-userid')
        with self.store.connect() as c:c.execute("UPDATE users SET active=0 WHERE username='liruiqi'")
        with self.assertRaises(ValueError):operator_config(self.store)
