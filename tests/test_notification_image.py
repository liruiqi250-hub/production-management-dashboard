import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from app.notification_image import image_model,upload_png,add_image
from app.notification_cards import card_body
from app.dingtalk_notifications import Rejected
from app.store import Store

TEXT='【仅本人预览】办公群样式\n资料截至2026-10-17，不是今日完成。\n待处理共 7 项：生产 4、售后 3；重点关注 2 项。\n各跟进人：张英 4；待分派 3。\n• 生产｜合成产品调整｜张英：反馈实际阶段。\n• 售后｜合成资料核对｜待分派：核对来源。'
PNG=b'\x89PNG\r\n\x1a\n'+b'\0'*40


class ImageTests(unittest.TestCase):
    def test_model_uses_existing_numbers_and_at_most_three_points(self):
        model=image_model(TEXT+'\n• 三\n• 四')
        self.assertEqual([m['value'] for m in model['metrics']],['7','4','3'])
        self.assertEqual(model['urgent'],'2 项重点关注');self.assertEqual(len(model['points']),3)
        self.assertIn('2026-10-17',model['source']);self.assertIn('待分派',model['workload'])
        with self.assertRaises(ValueError):image_model('统计未核验')

    def test_upload_only_png_and_size_limit_before_network(self):
        client=Mock();client.token='example-token'
        for data in (b'not-png',PNG+b'x'*(2*1024*1024)):
            with self.assertRaises(ValueError):upload_png(client,data)
        client.prepare.assert_not_called()

    def test_upload_fixed_https_destination_and_safe_filename(self):
        client=Mock();client.token='example-token-with+plus'
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'errcode':0,'media_id':'@example-media'}).encode()
        opener=Mock();opener.open.return_value=response
        with patch('app.notification_image.urllib.request.build_opener',return_value=opener):
            self.assertEqual(upload_png(client,PNG),'@example-media')
        request=opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://oapi.dingtalk.com/media/upload?'))
        self.assertIn('%2B',request.full_url);self.assertIn(b'filename="daily-summary.png"',request.data)
        self.assertNotIn('合成产品'.encode(),request.data)

    def test_upload_failure_never_exposes_token_or_raw_error(self):
        client=Mock();client.token='secret-example-token'
        opener=Mock();opener.open.side_effect=RuntimeError('raw secret-example-token')
        with patch('app.notification_image.urllib.request.build_opener',return_value=opener):
            with self.assertRaises(Rejected) as error:upload_png(client,PNG)
        self.assertNotIn('secret-example-token',str(error.exception))

    def test_embedded_image_keeps_same_buttons_and_body_under_limit(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);store=Store(Path(temp.name))
        card=card_body(TEXT,'https://example.example.workers.dev','a'*32)
        with patch('app.notification_image.render_image',return_value=PNG),patch('app.notification_image.upload_png',return_value='@example-media'):
            result=add_image(store,Mock(),TEXT,card)
        before=json.loads(card)['notification_card']['msgParam'];after=json.loads(result)['notification_card']['msgParam']
        self.assertTrue(after['text'].startswith('![日报摘要](@example-media)'));self.assertLess(len(after['text']),500)
        for key in ['actionURL1','actionURL2','actionTitle1','actionTitle2']:self.assertEqual(before[key],after[key])

    def test_image_failure_falls_back_to_original_card(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);store=Store(Path(temp.name))
        card=card_body(TEXT,'https://example.example.workers.dev','a'*32)
        with patch('app.notification_image.render_image',side_effect=RuntimeError('synthetic')):
            self.assertEqual(add_image(store,Mock(),TEXT,card),card)
        self.assertEqual(json.loads((store.private/'notifications/image_status.json').read_text('utf-8'))['state'],'fallback')

    def test_long_title_card_also_stays_under_limit(self):
        card=json.loads(card_body('长标题'*100+'\n'+'内容'*500,'https://example.example.workers.dev','a'*32))
        self.assertLess(len(card['notification_card']['msgParam']['text']),500)
