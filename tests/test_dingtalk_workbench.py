"""真实HTTP及服务端免登边界；授权码/身份均为合成样本。"""
import json
import unittest
from unittest.mock import patch
from app.dingtalk_login import LoginError
from app.store import digest
from tests.test_dingtalk_login import DingTalkFixture, DingTalkHttpTests


class WorkbenchTests(DingTalkFixture, unittest.TestCase):
    def login(self, subject='example-union-subject'):
        public,cookie=self.ding.workbench_start()
        with patch.object(self.ding,'exchange_workbench',return_value=subject):
            return self.ding.workbench_login(public['nonce'],cookie,'example-native-code')

    def test_native_unionid_reuses_existing_oauth_binding_and_current_role(self):
        self.bind()
        with self.store.connect() as c:c.execute("UPDATE users SET role='viewer' WHERE username='exampleuser'")
        result=self.login()['session']
        self.assertEqual(result['user']['username'],'exampleuser')
        self.assertEqual(self.store.authenticate(result['token'])['role'],'viewer')

    def test_unknown_dingtalk_user_requires_original_explicit_password_binding(self):
        result=self.login('unknown-example-subject')
        self.assertIn('pending',result)
        self.assertNotIn('session',result)
        self.assertEqual(len(self.store.users()),4)
        with self.assertRaises(LoginError):self.ding.bind_pending(result['pending'],'example-forged-session')

    def test_wrong_cookie_expiry_and_replay_do_not_exchange_code(self):
        public,cookie=self.ding.workbench_start()
        with patch.object(self.ding,'exchange_workbench',return_value='example-subject') as exchange:
            with self.assertRaises(LoginError):self.ding.workbench_login(public['nonce'],'other-browser','example-code')
            exchange.assert_not_called()
            self.ding.workbench_login(public['nonce'],cookie,'example-code')
            with self.assertRaises(LoginError):self.ding.workbench_login(public['nonce'],cookie,'example-code')
            self.assertEqual(exchange.call_count,1)
        public,cookie=self.ding.workbench_start()
        with self.store.connect() as c:c.execute("UPDATE dingtalk_workbench_states SET expires='2000-01-01'")
        with patch.object(self.ding,'exchange_workbench') as exchange:
            with self.assertRaises(LoginError):self.ding.workbench_login(public['nonce'],cookie,'example-code')
            exchange.assert_not_called()

    def test_oauth_state_cannot_be_used_as_native_nonce(self):
        from urllib.parse import parse_qs,urlsplit
        url,cookie=self.ding.start()
        with patch.object(self.ding,'exchange_workbench') as exchange:
            with self.assertRaises(LoginError):self.ding.workbench_login(parse_qs(urlsplit(url).query)['state'][0],cookie,'example-code')
            exchange.assert_not_called()

    def test_disabled_and_initial_password_rules_still_apply(self):
        self.bind()
        with self.store.connect() as c:c.execute("UPDATE users SET active=0 WHERE username='exampleuser'")
        with self.assertRaises(LoginError):self.login()
        with self.store.connect() as c:c.execute("UPDATE users SET active=1,must_change_password=1 WHERE username='exampleuser'")
        self.assertTrue(self.login()['session']['user']['must_change_password'])

    def test_native_exchange_only_trusts_fixed_enterprise_unionid(self):
        answers=[{'access_token':'example-enterprise-token','expires_in':7200},
                 {'errcode':0,'result':{'unionid':'example-union-subject','sys':True,'sys_level':1,'name':'管理员'}}]
        with patch.object(self.ding,'_api',side_effect=answers) as api:
            self.assertEqual(self.ding.exchange_workbench('example-native-code'),'example-union-subject')
            self.assertIn('/'+self.config['corp_id']+'/token',api.call_args_list[0].args[0])
            self.assertEqual(api.call_args_list[0].args[1]['grant_type'],'client_credentials')
            self.assertIn('/topapi/v2/user/getuserinfo?',api.call_args_list[1].args[0])
            self.assertEqual(api.call_args_list[1].args[1],{'code':'example-native-code'})
        with patch.object(self.ding,'_api',return_value=answers[1]) as api:
            self.ding.exchange_workbench('example-native-code-2')
            self.assertEqual(api.call_count,1)

    def test_native_errors_and_missing_unionid_never_fall_back_to_name(self):
        self.ding._app_token=('example-app-token',float('inf'))
        for response in ({'errcode':40029}, {'errcode':0,'result':{'name':'合成员工','userid':'example-user-id'}}, {'errcode':0,'result':None}):
            with self.subTest(response=response),patch.object(self.ding,'_api',return_value=response):
                with self.assertRaises(LoginError):self.ding.exchange_workbench('example-code')


class WorkbenchHttpTests(DingTalkHttpTests):
    # 复用已有HTTP fixture及原OAuth回归；新增免登入口独立验证。
    def native_request(self,data,cookie='',origin=True):
        import http.client
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'Content-Type':'application/json','Cookie':cookie}
        if origin:headers['Origin']=f'http://127.0.0.1:{self.server.server_port}'
        client.request('POST','/api/auth/dingtalk/workbench/login',json.dumps(data),headers)
        response=client.getresponse();result=(response.status,response.getheaders(),json.loads(response.read()));client.close();return result

    def challenge(self):
        status,headers,body=self.request('GET','/api/auth/dingtalk/workbench/start')
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',str(headers));self.assertIn('SameSite=Strict',str(headers))
        return json.loads(body),'dino_ding_workbench='+self.cookies(headers)['dino_ding_workbench'].value

    def test_native_http_login_requires_origin_cookie_and_nonce(self):
        public,cookie=self.challenge();data={'nonce':public['nonce'],'code':'example-code','username':'liruiqi','corpId':'other-enterprise'}
        with patch.object(self.ding,'exchange_workbench',return_value='example-subject') as exchange:
            self.assertEqual(self.native_request(data,cookie,origin=False)[0],403)
            self.assertEqual(self.native_request(data)[0],400)
            exchange.assert_not_called()
            status,headers,body=self.native_request(data,cookie)
            self.assertEqual(status,200)
            self.assertTrue(body['binding_pending'])
            self.assertEqual(self.cookies(headers)['dino_session'].value,'')
            self.assertEqual(self.native_request(data,cookie)[0],400)

    def test_bound_native_http_session_reuses_employee_permissions(self):
        self.bind();public,cookie=self.challenge()
        with patch.object(self.ding,'exchange_workbench',return_value='example-union-subject'):
            status,headers,body=self.native_request({'nonce':public['nonce'],'code':'example-code'},cookie)
        self.assertEqual(status,200);self.assertFalse(body['binding_pending'])
        session=self.cookies(headers)['dino_session'].value
        status,_,body=self.request('GET','/api/me',cookie='dino_session='+session)
        value=json.loads(body)
        self.assertEqual(value['user']['username'],'exampleuser')
        self.assertFalse(value['user']['permissions']['manage_all'])

    def test_starting_new_native_identity_clears_older_pending_binding(self):
        pending=self.callback('example-previous-person')['pending']
        status,headers,_=self.request('GET','/api/auth/dingtalk/workbench/start',cookie='dino_ding_binding='+pending)
        self.assertEqual(status,200)
        self.assertFalse(self.ding.status(pending)['binding_pending'])
        self.assertEqual(self.cookies(headers)['dino_ding_binding'].value,'')
