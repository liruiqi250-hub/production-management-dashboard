"""独立通知应用；不改变工作台登录或绑定，固定收件配置仅保存在本机。"""
import json
import re


IDENT = re.compile(r'[A-Za-z0-9_+=:/.-]{1,512}')
NAMES = {'liruiqi': '李睿琪', 'zengyue': '曾悦'}


def independent_sender(store, config):
    if config.get('sender_profile') != 'independent':
        return None
    path = store.private / 'dingtalk/notification_sender.json'
    value = json.loads(path.read_text('utf-8-sig'))
    credentials = value.get('credentials', {})
    if (value.get('schema') != 1 or value.get('verified') is not True
            or not credentials.get('corp_id') or not credentials.get('client_id')
            or not credentials.get('client_secret')
            or config.get('robot_code') != credentials['client_id']):
        raise ValueError('独立通知应用尚未核验')
    routes = value.get('routes', {})
    if set(routes) != set(NAMES):
        raise ValueError('独立通知仅允许已核验的两名固定收件人')
    for username, name in NAMES.items():
        route = routes[username]
        if (route.get('username') != username or route.get('name') != name
                or route.get('verified') is not True or not route.get('verified_at')
                or route.get('corp_id') != credentials['corp_id']
                or not isinstance(route.get('user_id'), str)
                or not IDENT.fullmatch(route['user_id'])):
            raise ValueError('独立通知收件身份尚未核验')
        with store.connect() as db:
            user = db.execute('SELECT active FROM users WHERE username=?', (username,)).fetchone()
        if not user or not user['active']:
            raise ValueError('固定收件账号已停用或不存在')
    if routes['liruiqi']['user_id'] == routes['zengyue']['user_id']:
        raise ValueError('两名收件人不能共用同一身份')
    return credentials, routes


def independent_config(store, config, origin):
    loaded = independent_sender(store, config)
    if loaded is None:
        return None
    credentials, routes = loaded
    if not origin:
        raise ValueError('通知看板入口尚未配置')
    value = dict(config)
    leader = dict(value.get('leader', {}))
    channels = []
    if leader.get('enabled', True) is True:
        if leader.get('username') != 'zengyue':
            raise ValueError('领导通知只能发送给曾悦')
        leader.update(routes['zengyue'])
        channels.append('leader')
    value['leader'] = leader
    group = value.get('group', {})
    if group.get('enabled') is True:
        if (group.get('name') != '办公群' or group.get('verified') is not True
                or group.get('corp_id') != credentials['corp_id']
                or not isinstance(group.get('open_conversation_id'), str)
                or not IDENT.fullmatch(group['open_conversation_id'])):
            raise ValueError('办公群尚未核验为通知应用所属企业')
        channels.insert(0, 'group')
    if not channels:
        raise ValueError('没有已启用的日报接收通道')
    value['channels'] = channels
    return value, credentials, origin
