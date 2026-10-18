"""冻结日报、仅本人卡片预览与整理失败通知；所有业务文件只在本机。"""
from datetime import datetime, time
import argparse
import hashlib
import json
from pathlib import Path
import re
import uuid

from app.store import Store, TZ
from app.dingtalk_notifications import Notifications, summaries, day_of


def freeze_report(store, snapshot, end, label):
    from app.reports import build_report
    from app import __version__
    report = build_report(snapshot, 'daily', end)
    report.update(version=__version__, notification_label=label)
    report['limitations'].insert(0, label + '；本文件固定为发送时归档内容。')
    # 同一份内容复用同一文件，重试不产生不同的PDF地址。
    content = json.dumps(report, ensure_ascii=False, sort_keys=True)
    ident = hashlib.sha256(content.encode()).hexdigest()[:32]
    folder = store.private / 'notifications/reports'; folder.mkdir(parents=True, exist_ok=True)
    path = folder / (ident + '.json')
    if not path.exists():
        temporary = folder / (uuid.uuid4().hex + '.tmp')
        temporary.write_text(content, 'utf-8'); temporary.replace(path)
    return ident


def load_report(store, ident):
    if not re.fullmatch(r'[a-f0-9]{32}', ident or ''):
        raise ValueError('日报标识无效，请从原卡片重新打开')
    path = store.private / 'notifications/reports' / (ident + '.json')
    if not path.is_file():
        raise ValueError('这份日报尚未归档，请联系李睿琪或从看板重新导出')
    return json.loads(path.read_text('utf-8'))


def card_body(text, origin, report_id):
    lines = text.splitlines()
    # 钉钉样式由官方客户端绘制；摘要正文限500字内，完整明细由按钮进入。
    title = lines[0][:70]
    chosen = [line for line in lines[1:] if not line.startswith(('打开看板：','三天计时','仅统计'))]
    brief = '\n\n'.join(chosen)[:min(430,490-len(title))]
    params = {'title': title, 'text': '### ' + title + '\n\n' + brief,
              'actionTitle1': '打开看板', 'actionURL1': origin + '/',
              'actionTitle2': '下载日报 PDF', 'actionURL2': origin + '/?report=' + report_id}
    return json.dumps({'notification_card': {'msgKey': 'sampleActionCard2', 'msgParam': params}}, ensure_ascii=False)


def operator_config(store):
    from app.dingtalk_login import DingTalkLogin
    from app.remote_access import RemoteGateway
    folder = store.private / 'dingtalk'
    value = json.loads((folder / 'notifications.json').read_text('utf-8-sig'))
    if value.get('push_paused') is True:
        raise ValueError('通知已由管理员暂停')
    if value.get('sender_profile') == 'independent':
        from app.notification_transport import independent_sender
        if value.get('operator_alerts_enabled') is not True:
            raise ValueError('李睿琪异常提醒未启用')
        credentials, routes = independent_sender(store, value)
        origin = RemoteGateway(store).origin
        if not origin:
            raise ValueError('通知看板入口尚未配置')
        return value, credentials, origin, routes['liruiqi']['user_id']
    credentials = DingTalkLogin(store).config
    origin = RemoteGateway(store).origin
    route = json.loads((folder / 'liruiqi_notification_route.json').read_text('utf-8'))
    if (not value.get('operator_alerts_enabled') or not credentials or not origin
            or value.get('robot_code') != credentials['client_id'] or route.get('username') != 'liruiqi'
            or route.get('corp_id') != credentials['corp_id']):
        raise ValueError('李睿琪收件身份尚未核验')
    if not isinstance(route.get('user_id'),str) or not re.fullmatch(r'[A-Za-z0-9_+=:/.-]{1,512}',route['user_id']):
        raise ValueError('本人收件标识无效')
    with store.connect() as c:
        row = c.execute('SELECT d.subject_hash FROM dingtalk_identities d JOIN users u ON d.username=u.username '
                        "WHERE d.corp_id=? AND d.username='liruiqi' AND u.active=1", (credentials['corp_id'],)).fetchone()
    if not row or row['subject_hash'] != route.get('subject_hash'):
        raise ValueError('李睿琪当前绑定已改变，需本人重新打开工作台核验')
    return value, credentials, origin, route['user_id']


def preview(store, *, at=None, service=None, with_image=False):
    from app.after_sales import AfterSales
    at = at or datetime.now(TZ)
    value, credentials, origin, target = operator_config(store)
    snapshot = store.snapshot(); snapshot['after_sales'] = AfterSales(store).snapshot()
    imported = day_of(snapshot.get('imported_at'))
    if not imported:
        raise ValueError('暂无已导入资料，不能生成业务卡片预览')
    messages = summaries(snapshot, origin, at)
    report_id = freeze_report(store, snapshot, imported, '卡片预览，资料截至' + imported)
    service = service or Notifications(store)
    for channel, title in [('group','办公群样式'), ('leader','曾悦专报样式')]:
        lines = messages[channel].splitlines()
        lines[0] = '【仅本人预览】' + title
        lines[1] = '资料截至 ' + imported + '；仅展示样式，不是今日整理已完成。'
        # 去除以预览当天重新统计的新增/完成，避免旧资料被误写为今天数据。
        lines = [line for line in lines if not line.startswith('今日新增生产事项')]
        body = card_body('\n'.join(lines), origin, report_id)
        revision='image-v1' if with_image else 'v1'
        key = hashlib.sha256(f'card-preview-{revision}|{at.date()}|{target}|{channel}'.encode()).hexdigest()
        if with_image:
            with service.connect() as c:
                existing=c.execute('SELECT state FROM outbox WHERE id=?',(key,)).fetchone()
            if existing:
                continue
            from app.notification_image import add_image
            from app.dingtalk_notifications import RobotClient
            client=service.client or RobotClient(credentials,value['robot_code'])
            body=add_image(store,client,'\n'.join(lines),body)
        service.send_one(key, str(at.date()), 'operator-preview', target, body, 'card-preview', credentials, value['robot_code'])
    return {'state':'checked','channels':service.status(str(at.date()))}


def check_failure(store, state, *, at=None, deadline=False, service=None):
    at = at or datetime.now(TZ)
    day = str(at.date())
    # 15:30前人工试跑不触发每日失败通知；截止检查从16:50开始。
    if at.time() < time(15,30) or deadline and at.time() < time(16,50):
        return {'state':'deferred'}
    today = day_of(state.get('started_at')) == day
    if today:
        started = datetime.fromisoformat(state['started_at']).astimezone(TZ)
        today = started.time() >= time(15,30)
    if today and state.get('state') == 'completed':
        return {'state':'completed'}
    if not deadline and (not today or state.get('state') not in {'failed','partial'}):
        return {'state':'no_failure'}
    value, credentials, origin, target = operator_config(store)
    detail = ('尚未找到当天成功整理记录' if not today else
              {'failed':'整理失败','partial':'数据部分导入，报表归档未完成'}.get(state.get('state'),'整理仍未完成'))
    late = '【延迟补发】' if at.time() >= time(17,0) else ''
    body = late + '【整理异常提醒】生产管理看板 · ' + day + '\n截至' + at.strftime('%H:%M') + '：' + detail
    body += '。未将旧日报作为今日结果发送。请打开看板查看整理状态；需要时手动重新运行。\n' + origin + '/'
    service = service or Notifications(store)
    key = hashlib.sha256(f'failure-alert|{day}|{target}'.encode()).hexdigest()
    service.send_one(key,day,'operator-alert',target,body,state.get('id','deadline-check'),credentials,value['robot_code'])
    return {'state':'checked','channels':service.status(day)}


def notify_failure(store, state):
    try:
        return check_failure(store,state)
    except Exception:
        return {'state':'failed','message':'本人整理异常提醒未完成，请核对本机配置与网络'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview-to-me',action='store_true')
    parser.add_argument('--preview-image-to-me',action='store_true')
    parser.add_argument('--check-deadline',action='store_true')
    args = parser.parse_args()
    store = Store(Path(__file__).resolve().parents[1])
    if args.preview_to_me or args.preview_image_to_me:
        result = preview(store,with_image=args.preview_image_to_me)
    elif args.check_deadline:
        from app.jobs import Jobs
        result = check_failure(store, Jobs(store.workspace).status(), deadline=True)
    else:
        result = {'state':'idle'}
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
