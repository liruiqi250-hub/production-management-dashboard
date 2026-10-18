"""同一份日报正文绘制摘要图，上传钉钉媒体，不开放公开图床。"""
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import urllib.request
from urllib.parse import urlencode
import uuid

from app.dingtalk_notifications import NoRedirect, Rejected


def image_model(text):
    lines = text.splitlines()
    count = next((line for line in lines if line.startswith('待处理共 ')), '')
    numbers = re.search(r'待处理共 (\d+) 项：生产 (\d+)、售后 (\d+)；重点关注 (\d+) 项',count)
    if not numbers:
        raise ValueError('日报统计不完整，不能生成摘要图')
    total, production, after, urgent = numbers.groups()
    points=[]
    for line in lines:
        if line.startswith('• '):
            points.append(line[2:])
        elif points and line.startswith(('跟进：','下一步/当前情况：')):
            points[-1] += '\n' + line
    workload = next((line for line in lines if line.startswith('各跟进人：')), '')
    return {'title':lines[0][:55], 'source':lines[1][:90],
            'metrics':[{'label':'待处理合计','value':total},{'label':'生产待处理','value':production},{'label':'售后待处理','value':after}],
            'urgent':urgent+' 项重点关注', 'points':[p[:220] for p in points[:3]],
            'workload':workload, 'footer':'仅统计已登记事项；阶段推进不等于整单完工。详情与证据见看板。'}


def render_image(store, text):
    model = image_model(text)
    folder = store.private / 'notifications/images';folder.mkdir(parents=True,exist_ok=True)
    ident = uuid.uuid4().hex
    source, target = folder/(ident+'.json'), folder/(ident+'.png')
    source.write_text(json.dumps(model,ensure_ascii=False), 'utf-8')
    powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    script=Path(__file__).resolve().parents[1]/'scripts/render_notification_image.ps1'
    result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-WindowStyle','Hidden','-File',str(script),'-InputPath',str(source),'-OutputPath',str(target)],
                          capture_output=True,timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or not target.is_file():
        raise RuntimeError('日报摘要图生成失败，文字卡片继续保留')
    data=target.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',data[16:24]) != (1080,1280):
        raise RuntimeError('日报摘要图尺寸核验失败')
    return data


def upload_png(client, data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or not 24 <= len(data) <= 2*1024*1024:
        raise ValueError('只允许上传不超过2MB的本轮PNG摘要图')
    if not client.token:
        client.prepare()
    boundary='Dashboard'+uuid.uuid4().hex
    body=(('--'+boundary+'\r\nContent-Disposition: form-data; name="type"\r\n\r\nimage\r\n--'+boundary+
           '\r\nContent-Disposition: form-data; name="media"; filename="daily-summary.png"\r\nContent-Type: image/png\r\n\r\n').encode()+data+('\r\n--'+boundary+'--\r\n').encode())
    request=urllib.request.Request('https://oapi.dingtalk.com/media/upload?'+urlencode({'access_token':client.token,'type':'image'}),data=body,
                                    headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(request,timeout=20) as response:
            result=json.loads(response.read(1024*1024))
        media=result.get('media_id')
        if result.get('errcode') != 0 or not isinstance(media,str) or not re.fullmatch(r'[@A-Za-z0-9_+=:/.-]{1,512}',media):
            raise ValueError()
    except Exception:
        raise Rejected('摘要图上传未完成，文字卡片继续保留；请核对本机媒体配置和网络') from None
    return media


def add_image(store, client, text, card):
    """只增加一张钉钉媒体图片，统计、按钮、收件人均不改变。"""
    try:
        media=upload_png(client,render_image(store,text))
        value=json.loads(card);params=value['notification_card']['msgParam']
        image='![日报摘要]('+media+')\n\n'
        params['text']=image+params['text'][:max(0,495-len(image))]
        result=json.dumps(value,ensure_ascii=False)
        status={'state':'ready'}
    except Exception:
        result=card;status={'state':'fallback','message':'摘要图未完成，保留原文字卡片及两个按钮'}
    folder=store.private/'notifications';folder.mkdir(parents=True,exist_ok=True)
    (folder/'image_status.json').write_text(json.dumps(status,ensure_ascii=False),'utf-8')
    return result
