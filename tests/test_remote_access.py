import http.client
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from app.remote_access import RemoteGateway
from app.dingtalk_login import DingTalkLogin
from tests import test_dingtalk_login as base

ORIGIN='https://dashboard.example.workers.dev'
SECRET='example-'+('x'*43)


class GatewayTests(base.DingTalkFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.path=self.store.private/'remote_access'/(self.store.path.stem+'.json')
        self.path.parent.mkdir()
        self.value={'enabled':True,'public_origin':ORIGIN,'gateway_secret':SECRET}
        self.save()

    def save(self):self.path.write_text(json.dumps(self.value),'utf-8')

    def test_gateway_requires_loopback_exact_secret_and_origin(self):
        g=RemoteGateway(self.store);h={'X-Dino-Gateway-Key':SECRET,'X-Dino-Public-Origin':ORIGIN}
        self.assertEqual(g.verify('127.0.0.1',h),ORIGIN)
        self.assertIsNone(g.verify('192.168.10.2',h))
        self.assertIsNone(g.verify('127.0.0.1',{'X-Forwarded-Proto':'https','X-Forwarded-Host':ORIGIN}))
        self.assertIsNone(g.verify('127.0.0.1',{**h,'X-Dino-Gateway-Key':'example-wrong'}))
        self.assertIsNone(g.verify('127.0.0.1',{**h,'X-Dino-Gateway-Key':'非ASCII'}))
        self.assertIsNone(g.verify('127.0.0.1',{**h,'X-Dino-Public-Origin':'https://other.example'}))

    def test_gateway_invalid_or_disabled_config_fails_closed(self):
        for origin in ['http://example.com','https://127.0.0.1','https://example.com/path','https://example.com?x=1','https://example.com#x']:
            self.value['public_origin']=origin;self.save()
            self.assertIsNone(RemoteGateway(self.store).origin)
        self.value['public_origin']=ORIGIN;self.value['enabled']=False;self.save()
        self.assertIsNone(RemoteGateway(self.store).origin)


class GatewayHttpTests(base.DingTalkHttpTests):
    def setUp(self):
        super().setUp()
        folder=self.store.private/'remote_access';folder.mkdir()
        (folder/(self.store.path.stem+'.json')).write_text(json.dumps({'enabled':True,'public_origin':ORIGIN,'gateway_secret':SECRET}),'utf-8')
        self.server.gateway=RemoteGateway(self.store)
        self.server.dingtalk=self.ding=DingTalkLogin(self.store,public_origin=ORIGIN)

    def remote(self,method,path,data=None,cookie='',origin=ORIGIN,secret=SECRET):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'X-Dino-Gateway-Key':secret,'X-Dino-Public-Origin':ORIGIN,'Content-Type':'application/json','Cookie':cookie}
        if origin is not None:headers['Origin']=origin
        client.request(method,path,json.dumps(data) if data is not None else None,headers)
        response=client.getresponse();result=(response.status,response.getheaders(),response.read());client.close();return result

    def test_public_headers_need_gateway_secret_before_any_route(self):
        self.assertEqual(self.remote('GET','/health',secret='example-wrong')[0],403)
        self.assertEqual(self.remote('GET','/api/dashboard')[0],401)
        self.assertEqual(self.remote('GET','/')[0],200)

    def test_public_login_adds_secure_cookie_and_preserves_employee_role(self):
        status,headers,body=self.remote('POST','/api/login',{'username':'exampleuser','password':base.PASSWORD})
        self.assertEqual(status,200,body)
        self.assertTrue(self.cookies(headers)['dino_session']['secure'])
        self.assertIn('HttpOnly',str(headers));self.assertIn('SameSite=Strict',str(headers))
        cookie='dino_session='+self.cookies(headers)['dino_session'].value
        _,_,body=self.remote('GET','/api/me',cookie=cookie)
        data=json.loads(body)
        self.assertEqual(data['lan_url'],ORIGIN)
        self.assertEqual(data['user']['username'],'exampleuser')
        self.assertFalse(data['user']['permissions']['manage_all'])

    def test_public_mutations_require_exact_https_origin(self):
        for origin in [None,'http://127.0.0.1:'+str(self.server.server_port),'https://evil.example']:
            self.assertEqual(self.remote('POST','/api/login',{'username':'exampleuser','password':base.PASSWORD},origin=origin)[0],403)
        status,headers,_=self.request('POST','/api/login',{'username':'exampleuser','password':base.PASSWORD})
        self.assertEqual(status,200)
        self.assertFalse(self.cookies(headers)['dino_session']['secure'])

    def test_oauth_public_start_uses_only_configured_https_callback(self):
        status,headers,_=self.remote('GET','/auth/dingtalk/start?redirect_uri=https://evil.example')
        self.assertEqual(status,303)
        query=parse_qs(urlsplit(dict(headers)['Location']).query)
        self.assertEqual(query['redirect_uri'],[ORIGIN+'/auth/dingtalk/callback'])
        self.assertTrue(self.cookies(headers)['dino_ding_oauth']['secure'])
        _,_,body=self.remote('GET','/api/auth/dingtalk')
        self.assertEqual(json.loads(body)['login_url'],ORIGIN+'/auth/dingtalk/start')

    def test_native_public_login_uses_same_binding_and_secure_cookie(self):
        self.bind()
        _,headers,body=self.remote('GET','/api/auth/dingtalk/workbench/start')
        data={'nonce':json.loads(body)['nonce'],'code':'example-native-code'}
        cookie='dino_ding_workbench='+self.cookies(headers)['dino_ding_workbench'].value
        with patch.object(self.ding,'exchange_workbench',return_value='example-union-subject'):
            status,headers,body=self.remote('POST','/api/auth/dingtalk/workbench/login',data,cookie=cookie)
        self.assertEqual(status,200,body)
        self.assertFalse(json.loads(body)['binding_pending'])
        self.assertTrue(self.cookies(headers)['dino_session']['secure'])
