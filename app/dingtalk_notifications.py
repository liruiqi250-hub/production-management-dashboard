"""固定办公群及曾悦日报；正文、接收者、发件箱与凭据只保存在本机。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, time
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.request

from app.store import Store, TZ, now


class Rejected(ValueError):
    """平台明确拒绝，允许维护人员重试；不得包含平台原始响应。"""


def compact(value, limit=90):
    value = re.sub(r'https?://\S+', '（链接见看板）', str(value or ''))
    value = re.sub(r'[\r\n\t]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def day_of(value):
    try:
        return datetime.fromisoformat(value).astimezone(TZ).date().isoformat()
    except (TypeError, ValueError):
        return None


def record_leader_route(store, corp_id, subject, user_id):
    """本人端内身份与既有绑定一致时，只保存曾悦的消息路由，不改变登录权限。"""
    from app.store import digest
    path = store.private / 'dingtalk/notifications.json'
    if not path.is_file():
        return
    config = json.loads(path.read_text('utf-8-sig'))
    if config.get('capture_leader_route') is not True or config.get('leader', {}).get('username') != 'zengyue':
        return
    if not isinstance(user_id, str) or not re.fullmatch(r'[A-Za-z0-9_+=:/.-]{1,512}', user_id):
        return
    subject_hash = digest(subject)
    with store.connect() as c:
        row = c.execute('SELECT u.username FROM dingtalk_identities d JOIN users u ON d.username=u.username '
                        'WHERE d.corp_id=? AND d.subject_hash=? AND u.username=? AND u.active=1',
                        (corp_id, subject_hash, 'zengyue')).fetchone()
    if not row:
        return
    route = {'username': 'zengyue', 'corp_id': corp_id, 'subject_hash': subject_hash, 'user_id': user_id,
             'verified_at': now(), 'source': '本人工作台免登身份与原绑定一致'}
    target = path.parent / 'zengyue_notification_route.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(route, ensure_ascii=False, indent=2), 'utf-8')
    temporary.replace(target)


def summaries(snapshot, origin, at):
    """只使用已入库状态/摘要，不解析微信原文，不重新推定负责人或完成。"""
    from app.reports import build_report
    from app.workdays import priority
    from app.office import DEFAULT_NAMES
    day = at.date().isoformat()
    report = build_report(snapshot, 'daily', day)
    production = [dict(i, scope='生产') for i in snapshot.get('items', []) if i.get('status') != '已解决']
    cases = snapshot.get('after_sales', {}).get('cases', [])
    after = [dict(i, scope='售后') for i in cases if i.get('include_in_total') is True and i.get('status') not in {'完成验收', '待并案'}]
    production = list({i['id']: i for i in production}.values())
    after = list({i['id']: i for i in after}.values())
    # 每个来源主键只计一次；待并案记录不加入合计。
    pending = list({(i['scope'], i['id']): i for i in production + after}.values())
    names = snapshot.get('office_staff') or DEFAULT_NAMES
    workloads = {name: sum(i.get('owner') == name for i in pending) for name in names}
    unassigned = sum(not i.get('owner') or i['owner'] not in workloads for i in pending)
    leaders = []
    for item in pending:
        reasons = []
        p = priority(item, at=at)
        if p['urgent']:
            reasons.append(str(p['workdays']) + '个工厂工作日未处理')
        deadline = str(item.get('deadline') or '')
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', deadline) and deadline < day:
            reasons.append('期限已过：' + deadline)
        brief = item.get('list_summary') or item.get('next_step') or item.get('next_action') or item.get('summary') or item.get('observed')
        if re.search(r'协调|拍板|需领导|双方.{0,8}未统一', str(item.get('title', '')) + str(brief or '')):
            reasons.append('已登记协调要求')
        if reasons:
            leaders.append((item, reasons, compact(brief)))
    leaders.sort(key=lambda row: (-len(row[1]), row[0].get('first_seen', ''), row[0]['id']))
    scope_note = '仅统计看板已登记事项；阶段推进不等于整单完工。'
    line = ('今日新增生产事项 ' + str(report['counts']['new']) + '，确认解决 ' + str(report['counts']['closed'])
            + '，阶段认可 ' + str(report['counts']['stage_confirmed']) + '。')
    counts = f'待处理共 {len(pending)} 项：生产 {len(production)}、售后 {len(after)}；重点关注 {len(leaders)} 项。'
    group = [f'生产与售后日报 · {day}', '截至 ' + at.strftime('%H:%M') + '，今日整理已更新。', line, counts,
             '各跟进人：' + '；'.join(f'{name} {number}' for name, number in workloads.items()) + f'；待分派 {unassigned}。']
    for item, reasons, brief in leaders[:3]:
        group.append(f'• {item["scope"]}｜{compact(item.get("title") or item.get("observed"), 38)}｜{compact(item.get("owner") or "待分派", 12)}：{brief[:60]}')
    if len(leaders) > 3:
        group.append(f'另 {len(leaders)-3} 项重点关注见看板。')
    leader = [f'曾悦 · 生产管理重点专报 · {day}', '截至 ' + at.strftime('%H:%M') + '，今日整理已更新。', line, counts]
    for item, reasons, brief in leaders[:8]:
        leader.extend([f'• {item["scope"]}｜{compact(item.get("title") or item.get("observed"), 48)}',
                       f'跟进：{compact(item.get("owner") or "待分派", 16)}；' + '；'.join(reasons), '下一步/当前情况：' + (brief or '待补充处理安排')])
    if not leaders:
        leader.append('当前没有已登记的逾期、三天未处理或明确协调事项。')
    elif len(leaders) > 8:
        leader.append(f'另 {len(leaders)-8} 项见看板重点清单。')
    for lines in (group, leader):
        lines.extend([scope_note, '三天计时沿用工厂规则：周末计入，仅扣已核验放假日。', '打开看板：' + origin + '/'])
    return {'group': '\n'.join(group), 'leader': '\n'.join(leader)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class RobotClient:
    def __init__(self, credentials, robot_code):
        self.credentials, self.robot_code = credentials, robot_code
        self.token = None

    def _post(self, path, body, *, token=None):
        request = urllib.request.Request('https://api.dingtalk.com' + path,
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={'Content-Type': 'application/json', **({'x-acs-dingtalk-access-token': token} if token else {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=20) as response:
                result = json.loads(response.read(1024 * 1024))
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise Rejected('钉钉明确拒绝请求，请核验权限、接收者和限流后重试') from None
            raise OSError('钉钉发送结果待核验') from None
        if not isinstance(result, dict):
            raise OSError('钉钉返回结果待核验')
        return result

    def prepare(self):
        if self.token:
            return
        c = self.credentials
        try:
            response = self._post('/v1.0/oauth2/' + c['corp_id'] + '/token', {
                'client_id': c['client_id'], 'client_secret': c['client_secret'], 'grant_type': 'client_credentials'})
            token = response.get('access_token')
            if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{8,4096}', token):
                raise ValueError()
            self.token = token
        except Exception:
            raise Rejected('应用令牌未取得，本次尚未发送消息') from None

    def send(self, channel, target, text):
        body = {'robotCode': self.robot_code, 'msgKey': 'sampleMarkdown',
                'msgParam': json.dumps({'title': text.splitlines()[0], 'text': text}, ensure_ascii=False)}
        if channel == 'group':
            body['openConversationId'] = target
            path = '/v1.0/robot/groupMessages/send'
        else:
            body['userIds'] = [target]
            path = '/v1.0/robot/oToMessages/batchSend'
        response = self._post(path, body, token=self.token)
        if response.get('invalidStaffIdList') or response.get('flowControlledStaffIdList'):
            raise Rejected('曾悦账号未接受消息，请核对企业身份或发送限制')
        key = response.get('processQueryKey')
        if not isinstance(key, str) or not key:
            raise OSError('消息回执待核验，禁止自动重复发送')
        return key


class Notifications:
    def __init__(self, store, *, client=None):
        self.store, self.client = store, client
        self.folder = store.private / 'notifications'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.database = self.folder / 'outbox.sqlite3'
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS outbox(
                id TEXT PRIMARY KEY,day TEXT,channel TEXT,target TEXT,body TEXT,job_id TEXT,
                state TEXT,attempts INTEGER DEFAULT 0,updated_at TEXT,receipt TEXT,error TEXT)''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.database, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def config(self):
        path = self.store.private / 'dingtalk/notifications.json'
        if not path.is_file():
            return None
        value = json.loads(path.read_text('utf-8-sig'))
        if value.get('enabled') is not True:
            return None
        from app.dingtalk_login import DingTalkLogin
        from app.remote_access import RemoteGateway
        ding, gateway = DingTalkLogin(self.store), RemoteGateway(self.store)
        group, leader = value['group'], dict(value['leader'])
        if not leader.get('user_id'):
            route_path = path.parent / 'zengyue_notification_route.json'
            if route_path.is_file():
                route = json.loads(route_path.read_text('utf-8'))
                if route.get('username') == 'zengyue' and route.get('corp_id') == (ding.config or {}).get('corp_id'):
                    leader.update(user_id=route.get('user_id'), subject_hash=route.get('subject_hash'), verified=True)
        value['leader'] = leader
        if (not ding.enabled or not gateway.origin or value.get('robot_code') != ding.config['client_id']
                or group.get('name') != '办公群' or group.get('verified') is not True
                or leader.get('username') != 'zengyue' or leader.get('verified') is not True):
            raise ValueError('推送接收范围尚未核验')
        for target in (group.get('open_conversation_id'), leader.get('user_id')):
            if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_+=:/.-]{1,512}', target):
                raise ValueError('推送接收者标识无效')
        with self.store.connect() as c:
            row = c.execute('SELECT subject_hash FROM dingtalk_identities WHERE corp_id=? AND username=?',
                            (ding.config['corp_id'], 'zengyue')).fetchone()
            user = c.execute('SELECT active FROM users WHERE username=?', ('zengyue',)).fetchone()
        if not row or not user or not user['active'] or row['subject_hash'] != leader.get('subject_hash'):
            raise ValueError('曾悦现有绑定或账号状态已改变，需重新核验收件人')
        return value, ding.config, gateway.origin

    def status(self, day=None):
        with self.connect() as c:
            rows = c.execute('SELECT day,channel,state,attempts,updated_at,error FROM outbox ' +
                             ('WHERE day=? ' if day else '') + 'ORDER BY updated_at DESC LIMIT 30', (day,) if day else ()).fetchall()
        return [dict(r) for r in rows]

    def deliver(self, snapshot, job_id, *, at=None, retry_failed=False, test=False):
        at = at or datetime.now(TZ)
        cfg = self.config()
        if cfg is None:
            return {'state': 'disabled', 'message': '日报推送未启用'}
        value, credentials, origin = cfg
        day = at.date().isoformat()
        if not test:
            if day_of(snapshot.get('imported_at')) != day:
                raise ValueError('只允许发送今天成功导入后的日报，旧数据不能群发')
            if at.timetz().replace(tzinfo=None) < time(15, 30):
                return {'state': 'deferred', 'message': '15:30前的手动整理不自动群发'}
            messages = summaries(snapshot, origin, at)
            if at.timetz().replace(tzinfo=None) > time(16, 30):
                messages = {k: '【延迟更新】\n' + v for k, v in messages.items()}
        else:
            messages = {k: '【推送连通测试】生产管理看板\n此消息仅验证固定接收通道，不是今日生产日报。\n打开看板：' + origin + '/' for k in ('group', 'leader')}
        client = self.client or RobotClient(credentials, value['robot_code'])
        for channel, body in messages.items():
            target = value['group']['open_conversation_id'] if channel == 'group' else value['leader']['user_id']
            key = hashlib.sha256(f'{day}|{channel}|{target}|{"test" if test else "daily"}'.encode()).hexdigest()
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,?,?,?,?,?,0,?,?,?)',
                          (key, day, channel, target, body, job_id, 'pending', now(), '', ''))
                row = c.execute('SELECT * FROM outbox WHERE id=?', (key,)).fetchone()
                if row['state'] != 'pending' and not (retry_failed and row['state'] == 'rejected'):
                    continue
                # 首次快照和目标固定；重试不能替换消息正文。
                try:
                    client.prepare()
                except Rejected:
                    c.execute("UPDATE outbox SET state='rejected',error=?,updated_at=? WHERE id=?",
                              ('应用令牌未取得，尚未发送', now(), key))
                    continue
                c.execute("UPDATE outbox SET state='sending',attempts=attempts+1,updated_at=?,error='' WHERE id=?", (now(), key))
            try:
                receipt = client.send(channel, target, row['body'])
                state, error = 'accepted', ''
            except Rejected as exc:
                state, error, receipt = 'rejected', str(exc), ''
            except Exception:
                state, error, receipt = 'unknown', '发送结果不确定，请核验钉钉后处理；不会自动重发', ''
            with self.connect() as c:
                c.execute('UPDATE outbox SET state=?,receipt=?,error=?,updated_at=? WHERE id=?', (state, receipt, error, now(), key))
        return {'state': 'checked', 'channels': self.status(day), 'note': 'accepted表示平台接受，不等于已读；unknown/sending禁止自动重发'}


def completed(store, snapshot, job_id):
    """只由已完成归档的固定后台流程调用；通知失败不回滚业务更新。"""
    try:
        return Notifications(store).deliver(snapshot, job_id)
    except Exception:
        return {'state': 'failed', 'message': '日报已更新，钉钉推送未完成；请检查本机固定配置和发送记录'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='向已核验固定接收者发送一次无业务数据连通测试')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--retry-failed', action='store_true', help='仅重试明确拒绝，不重试结果不明的消息')
    args = parser.parse_args()
    store = Store(Path(__file__).resolve().parents[1])
    service = Notifications(store)
    if args.preview:
        from app.after_sales import AfterSales
        snapshot = store.snapshot(); snapshot['after_sales'] = AfterSales(store).snapshot()
        cfg = service.config()
        if not cfg:
            raise ValueError('请先核验并配置固定接收范围')
        path = service.folder / 'preview.json'
        path.write_text(json.dumps(summaries(snapshot, cfg[2], datetime.now(TZ)), ensure_ascii=False, indent=2), 'utf-8')
        print('预览已保存在本机private/notifications/preview.json，尚未发送')
    elif args.test:
        print(json.dumps(service.deliver({}, 'connectivity-test', test=True, retry_failed=args.retry_failed), ensure_ascii=False))
    elif args.retry_failed:
        from app.jobs import Jobs
        state = Jobs(store.workspace).status()
        if state.get('state') != 'completed' or day_of(state.get('finished_at')) != datetime.now(TZ).date().isoformat():
            raise ValueError('只可重试今天已完成整理的日报')
        from app.after_sales import AfterSales
        snapshot = store.snapshot(); snapshot['after_sales'] = AfterSales(store).snapshot()
        print(json.dumps(service.deliver(snapshot, state['id'], retry_failed=True), ensure_ascii=False))
    else:
        print(json.dumps({'channels': service.status()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
