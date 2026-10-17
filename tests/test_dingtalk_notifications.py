import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.dingtalk_notifications import Notifications, Rejected, RobotClient, summaries, completed, record_leader_route
from app.store import Store, TZ
from tests.test_store_server import synthetic_snapshot
from tests.test_dingtalk_login import DingTalkFixture

AT = datetime(2026, 10, 18, 16, 10, tzinfo=TZ)
ORIGIN = 'https://example.example.workers.dev'


class FakeClient:
    def __init__(self): self.calls = []; self.error = None
    def prepare(self): pass
    def send(self, channel, target, text):
        self.calls.append((channel, target, text))
        if self.error: raise self.error
        return 'example-receipt'


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.client = FakeClient()
        self.service = Notifications(self.store, client=self.client)
        self.config = ({'robot_code': 'example-client', 'group': {'open_conversation_id': 'example-group'},
                        'leader': {'user_id': 'example-zengyue'}}, {}, ORIGIN)
        self.patch = patch.object(self.service, 'config', return_value=self.config); self.patch.start()
        self.snapshot = synthetic_snapshot()
        self.snapshot.update(imported_at=AT.isoformat(), office_staff=['张英', '银小芹'])
        self.snapshot['items'] = []
        self.snapshot['period'].update(start='2026-10-12', end='2026-10-19', cutoff='2026-10-19')
        self.snapshot['after_sales'] = {'cases': []}

    def tearDown(self): self.patch.stop(); self.temp.cleanup()

    def test_disabled_never_obtains_token_or_sends(self):
        self.service.config.return_value = None
        self.assertEqual(self.service.deliver({}, 'example', at=AT)['state'], 'disabled')
        self.assertFalse(self.client.calls)

    def test_daily_fixed_recipients_and_idempotence_across_jobs(self):
        self.service.deliver(self.snapshot, 'job-one', at=AT)
        self.service.deliver(self.snapshot, 'job-two', at=AT, retry_failed=True)
        self.assertEqual([(a,b) for a,b,_ in self.client.calls], [('group','example-group'),('leader','example-zengyue')])
        self.assertEqual([r['state'] for r in self.service.status()], ['accepted','accepted'])
        self.assertNotIn('target', self.service.status()[0])

    def test_stale_snapshot_blocked_and_early_manual_deferred(self):
        self.snapshot['imported_at'] = '2026-10-17T16:00:00+08:00'
        with self.assertRaises(ValueError): self.service.deliver(self.snapshot, 'old', at=AT)
        self.snapshot['imported_at'] = AT.isoformat()
        self.assertEqual(self.service.deliver(self.snapshot, 'early', at=AT.replace(hour=9))['state'], 'deferred')
        self.assertFalse(self.client.calls)

    def test_late_success_label_and_next_day_own_key(self):
        self.service.deliver(self.snapshot, 'late', at=AT.replace(hour=17))
        self.assertTrue(self.client.calls[0][2].startswith('【延迟更新】'))
        tomorrow=AT.replace(day=19); self.snapshot['imported_at']=tomorrow.isoformat()
        self.service.deliver(self.snapshot, 'next-day', at=tomorrow)
        self.assertEqual(len(self.client.calls),4)

    def test_explicit_rejection_requires_explicit_retry(self):
        self.client.error=Rejected('平台明确拒绝')
        self.service.deliver(self.snapshot, 'one', at=AT)
        self.client.error=None
        self.service.deliver(self.snapshot, 'two', at=AT)
        self.assertEqual(len(self.client.calls),2)
        self.service.deliver(self.snapshot, 'three', at=AT,retry_failed=True)
        self.assertEqual(len(self.client.calls),4)
        self.assertTrue(all(r['state']=='accepted' for r in self.service.status()))

    def test_timeout_and_crash_sending_never_automatically_repeat(self):
        self.client.error=TimeoutError('example-secret-must-not-log')
        self.service.deliver(self.snapshot,'one',at=AT)
        self.client.error=None
        self.service.deliver(self.snapshot,'two',at=AT,retry_failed=True)
        self.assertEqual(len(self.client.calls),2)
        self.assertTrue(all(r['state']=='unknown' for r in self.service.status()))
        self.assertNotIn('example-secret',json.dumps(self.service.status()))
        with self.service.connect() as c:c.execute("UPDATE outbox SET state='sending'")
        self.service.deliver(self.snapshot,'three',at=AT,retry_failed=True)
        self.assertEqual(len(self.client.calls),2)

    def test_parallel_triggers_each_channel_once(self):
        errors=[]
        def run():
            try:self.service.deliver(self.snapshot,'same-day',at=AT)
            except Exception as exc:errors.append(type(exc).__name__)
        threads=[threading.Thread(target=run) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertFalse(errors);self.assertEqual(len(self.client.calls),2)

    def test_connectivity_test_has_no_business_report_and_separate_dedupe(self):
        self.service.deliver({},'connectivity',at=AT,test=True)
        self.service.deliver({},'again',at=AT,test=True)
        self.assertEqual(len(self.client.calls),2)
        self.assertIn('不是今日生产日报',self.client.calls[0][2])
        self.service.deliver(self.snapshot,'daily',at=AT)
        self.assertEqual(len(self.client.calls),4)

    def test_summary_ownership_counts_exclude_merged_and_closed_cases(self):
        item={'id':'example-1','title':'例：裂缝处理待协调','category':'问题','status':'处理中','owner':'张英',
              'first_seen':'2026-10-12T09:00:00+08:00','last_seen':'2026-10-18T09:00:00+08:00',
              'deadline':'2026-10-17','list_summary':'双方方案未统一，需协调；下步反馈修改结果。','evidence':[]}
        self.snapshot['items']=[item]
        base={'id':'after-example','observed':'售后机械调整','status':'待检查','owner':'银小芹',
              'include_in_total':True,'tracking_started_at':'2026-10-17T09:00:00+08:00','evidence':[]}
        self.snapshot['after_sales']['cases']=[base,dict(base,id='merged',status='待并案',include_in_total=False),dict(base,id='closed',status='完成验收')]
        messages=summaries(self.snapshot,ORIGIN,AT)
        self.assertIn('共 2 项：生产 1、售后 1',messages['group'])
        self.assertIn('张英 1；银小芹 1',messages['group'])
        self.assertIn('期限已过',messages['leader']);self.assertIn('已登记协调要求',messages['leader'])
        self.assertNotIn('期限已过',messages['group'])
        self.assertIn('6个工厂工作日未处理',messages['leader'])
        self.assertNotIn('微信原文',messages['group'])

    def test_hook_failure_does_not_fail_completed_business(self):
        with patch('app.dingtalk_notifications.Notifications.deliver',side_effect=ValueError('example-private-error')):
            result=completed(self.store,self.snapshot,'one')
        self.assertEqual(result['state'],'failed');self.assertNotIn('example-private',str(result))

    def test_real_config_defaults_closed_and_cannot_add_other_receivers(self):
        self.patch.stop()
        self.assertIsNone(self.service.config())
        folder=self.store.private/'dingtalk';folder.mkdir()
        (folder/'notifications.json').write_text(json.dumps({'enabled':False}),'utf-8')
        self.assertIsNone(self.service.config())

    def test_only_existing_zengyue_binding_can_capture_leader_route(self):
        from app.dingtalk_login import DingTalkLogin
        from app.store import digest
        DingTalkLogin(self.store)  # 配置未启用时不建身份表，下面仅用合成表。
        with self.store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS dingtalk_identities(corp_id TEXT,subject_hash TEXT,username TEXT,created_at TEXT)')
            c.execute('INSERT INTO dingtalk_identities VALUES(?,?,?,?)',('example-corp',digest('example-zeng-subject'),'zengyue','example-time'))
        folder=self.store.private/'dingtalk';folder.mkdir(exist_ok=True)
        (folder/'notifications.json').write_text(json.dumps({'capture_leader_route':True,'leader':{'username':'zengyue'}}),'utf-8')
        route=folder/'zengyue_notification_route.json'
        record_leader_route(self.store,'example-corp','other-subject','other-user')
        self.assertFalse(route.exists())
        record_leader_route(self.store,'example-corp','example-zeng-subject','example-zeng-id')
        value=json.loads(route.read_text('utf-8'));self.assertEqual(value['user_id'],'example-zeng-id')
        self.assertNotIn('example-zeng-subject',route.read_text('utf-8'))
        with self.store.connect() as c:c.execute("UPDATE users SET active=0 WHERE username='zengyue'")
        record_leader_route(self.store,'example-corp','example-zeng-subject','changed-user')
        self.assertEqual(json.loads(route.read_text('utf-8'))['user_id'],'example-zeng-id')


class ConfigTests(DingTalkFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from app.store import digest
        self.subject_hash=digest('example-zeng-subject')
        with self.store.connect() as c:
            c.execute('INSERT INTO dingtalk_identities VALUES(?,?,?,?)',(self.config['corp_id'],self.subject_hash,'zengyue','example-time'))
        remote=self.store.private/'remote_access';remote.mkdir()
        (remote/(self.store.path.stem+'.json')).write_text(json.dumps({'enabled':True,'public_origin':ORIGIN,'gateway_secret':'example-'+('x'*43)}),'utf-8')
        self.value={'enabled':True,'robot_code':self.config['client_id'],
                    'group':{'name':'办公群','verified':True,'open_conversation_id':'example-group'},
                    'leader':{'username':'zengyue','verified':True,'user_id':'example-zeng-id','subject_hash':self.subject_hash}}
        self.path=self.store.private/'dingtalk/notifications.json';self.save()
        self.service=Notifications(self.store)

    def save(self):self.path.write_text(json.dumps(self.value),'utf-8')

    def test_fixed_config_and_unknown_group_or_extra_leader_blocked(self):
        self.assertEqual(self.service.config()[0]['leader']['user_id'],'example-zeng-id')
        for field,value in [('name','别的群'),('verified',False)]:
            original=self.value['group'][field];self.value['group'][field]=value;self.save()
            with self.assertRaises(ValueError):self.service.config()
            self.value['group'][field]=original
        self.value['leader']['username']='otheruser';self.save()
        with self.assertRaises(ValueError):self.service.config()

    def test_changed_binding_or_inactive_leader_blocks_delivery(self):
        self.value['leader']['subject_hash']='example-wrong';self.save()
        with self.assertRaises(ValueError):self.service.config()
        self.value['leader']['subject_hash']=self.subject_hash;self.save()
        with self.store.connect() as c:c.execute("UPDATE users SET active=0 WHERE username='zengyue'")
        with self.assertRaises(ValueError):self.service.config()

    def test_same_corp_native_route_reused_without_contact_search(self):
        self.value['capture_leader_route']=True;self.value['leader']={'username':'zengyue'};self.save()
        with self.assertRaises(ValueError):self.service.config()
        record_leader_route(self.store,self.config['corp_id'],'example-zeng-subject','example-zeng-id')
        self.assertEqual(self.service.config()[0]['leader']['user_id'],'example-zeng-id')


class ClientTests(unittest.TestCase):
    def test_request_uses_only_fixed_api_paths_and_one_recipient(self):
        client=RobotClient({},'example-robot');client.token='example-token'
        with patch.object(client,'_post',return_value={'processQueryKey':'example-receipt'}) as post:
            client.send('group','example-group','example-report')
            path,body=post.call_args.args
            self.assertEqual(path,'/v1.0/robot/groupMessages/send');self.assertEqual(body['openConversationId'],'example-group')
            self.assertNotIn('userIds',body)
            client.send('leader','example-zengyue','example-report')
            self.assertEqual(post.call_args.args[1]['userIds'],['example-zengyue'])
            self.assertNotIn('openConversationId',post.call_args.args[1])

    def test_invalid_user_is_rejected_and_missing_receipt_is_unknown(self):
        client=RobotClient({},'example-robot');client.token='example-token'
        with patch.object(client,'_post',return_value={'invalidStaffIdList':['example-bad']}):
            with self.assertRaises(Rejected):client.send('leader','example-bad','example-report')
        with patch.object(client,'_post',return_value={}):
            with self.assertRaises(OSError):client.send('group','example-group','example-report')
