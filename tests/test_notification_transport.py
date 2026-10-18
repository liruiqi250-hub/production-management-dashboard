import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

from app.store import Store, TZ
from app.notification_transport import independent_sender, independent_config
from app.dingtalk_notifications import Notifications
from app.notification_cards import operator_config
from tests.test_dingtalk_notifications import FakeClient


class IndependentNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name))
        self.folder=self.store.private/'dingtalk';self.folder.mkdir(exist_ok=True)
        self.sender={'schema':1,'verified':True,
                     'credentials':{'corp_id':'example-notification-corp','client_id':'example-notification-app','client_secret':'example-local-only-secret'},
                     'routes':{name:{'username':name,'name':label,'corp_id':'example-notification-corp','user_id':'example-'+name,'verified':True,'verified_at':'2026-10-18T11:00:00+08:00'}
                               for name,label in [('liruiqi','李睿琪'),('zengyue','曾悦')]}}
        self.config={'enabled':True,'sender_profile':'independent','robot_code':'example-notification-app','operator_alerts_enabled':True,
                     'group':{'name':'办公群','enabled':False,'corp_id':'example-other-corp','open_conversation_id':'example-old-group'},
                     'leader':{'username':'zengyue','enabled':True}}
        self.write()

    def write(self):
        (self.folder/'notification_sender.json').write_text(json.dumps(self.sender,ensure_ascii=False),encoding='utf-8')
        (self.folder/'notifications.json').write_text(json.dumps(self.config,ensure_ascii=False),encoding='utf-8')

    def test_new_sender_does_not_need_or_modify_login_binding(self):
        with patch('app.remote_access.RemoteGateway') as gateway:
            gateway.return_value.origin='https://example.example.workers.dev'
            cfg,credentials,_=Notifications(self.store).config()
        self.assertEqual(cfg['channels'],['leader'])
        self.assertEqual(credentials['corp_id'],'example-notification-corp')
        self.assertFalse((self.folder/'production_V0.2.0.json').exists())

    def test_wrong_corp_extra_recipient_and_shared_identity_rejected(self):
        initial=copy.deepcopy(self.sender)
        for change in ['corp','extra','shared']:
            self.sender=copy.deepcopy(initial)
            if change=='corp':self.sender['routes']['zengyue']['corp_id']='example-other-corp'
            elif change=='extra':self.sender['routes']['other']=dict(self.sender['routes']['zengyue'])
            else:self.sender['routes']['zengyue']['user_id']=self.sender['routes']['liruiqi']['user_id']
            self.write()
            with self.assertRaises(ValueError):independent_sender(self.store,self.config)

    def test_enabling_foreign_office_group_is_blocked(self):
        self.config['group'].update(enabled=True,verified=True)
        with self.assertRaises(ValueError):independent_config(self.store,self.config,'https://example.example.workers.dev')

    def test_disabled_office_group_never_receives_even_when_old_id_is_present(self):
        client=FakeClient();service=Notifications(self.store,client=client)
        cfg=independent_config(self.store,self.config,'https://example.example.workers.dev')
        at=datetime(2026,10,18,16,10,tzinfo=TZ)
        snapshot={'items':[],'after_sales':{'cases':[]},'imported_at':at.isoformat()}
        with patch.object(service,'config',return_value=cfg):
            result=service.deliver(snapshot,'example-job',at=at)
            service.deliver(snapshot,'example-job-again',at=at)
        self.assertEqual([(channel,target) for channel,target,_ in client.calls],[('leader','example-zengyue')])
        self.assertEqual(result['disabled_channels'],['group'])

    def test_operator_uses_verified_independent_self_route_and_disabled_alerts_stop(self):
        with patch('app.remote_access.RemoteGateway') as gateway:
            gateway.return_value.origin='https://example.example.workers.dev'
            cfg,credentials,origin,target=operator_config(self.store)
            self.assertEqual(target,'example-liruiqi')
            self.assertEqual(credentials['corp_id'],'example-notification-corp')
            self.config['operator_alerts_enabled']=False;self.write()
            with self.assertRaises(ValueError):operator_config(self.store)

    def test_inactive_real_account_cannot_receive(self):
        with self.store.connect() as db:db.execute("UPDATE users SET active=0 WHERE username='zengyue'")
        with self.assertRaises(ValueError):independent_sender(self.store,self.config)

    def test_explicit_pause_stops_daily_and_operator_paths(self):
        self.config['push_paused']=True;self.write()
        self.assertIsNone(Notifications(self.store).config())
        with self.assertRaises(ValueError):operator_config(self.store)


if __name__=='__main__':unittest.main()
