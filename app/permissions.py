"""在当前数据库事务中按办公室跟进人校验写入权限。"""
from __future__ import annotations

import json
from app.office import office_policy, canonical_name


class ItemPermissionError(PermissionError):
    pass


def permission_profile(actor, workspace):
    office = office_policy(workspace)
    manager = actor.get('role') == 'admin' or actor.get('username') in {'liruiqi', 'zengyue'}
    name = canonical_name(actor.get('username'), office) or canonical_name(actor.get('display_name'), office)
    return {'manage_all': manager, 'office_name': name, 'can_write': manager or actor.get('role') == 'editor'}


def active_actor(conn, username):
    row = conn.execute('SELECT username,display_name,role FROM users WHERE username=? AND active=1', (username,)).fetchone()
    if not row:
        raise ItemPermissionError('操作账号无效或已停用')
    return dict(row)


def require_item_edit(conn, username, base, overrides, changes, workspace):
    actor = active_actor(conn, username)
    profile = permission_profile(actor, workspace)
    if profile['manage_all']:
        return actor
    if not profile['can_write']:
        raise ItemPermissionError('该账号仅有查看权限')
    office = office_policy(workspace)
    owner = canonical_name(overrides.get('owner', base.get('owner')), office)
    if not owner or not profile['office_name'] or owner != profile['office_name']:
        raise ItemPermissionError('只能修改自己跟进的事项，其他人员或待分派事项仅可查看')
    if 'owner' in changes and canonical_name(changes['owner'], office) != owner:
        raise ItemPermissionError('负责人分派或改派由李睿琪、曾悦操作')
    return actor


def require_piece_edit(conn, username, piece_id, workspace, *, associations=False):
    profile = permission_profile(active_actor(conn, username), workspace)
    if profile['manage_all']:
        return
    if associations or not profile['can_write']:
        raise ItemPermissionError('单件关联的建立或解除由李睿琪、曾悦操作' if associations else '该账号仅有查看权限')
    links = conn.execute('SELECT item_type,item_id FROM production_links WHERE piece_id=?', (piece_id,)).fetchall()
    if not links:
        raise ItemPermissionError('此单件尚未明确关联跟进事项，请由李睿琪或曾悦处理')
    for link in links:
        table = 'items' if link['item_type'] == 'production' else 'after_sales'
        row = conn.execute('SELECT base_json,override_json FROM '+table+' WHERE id=?', (link['item_id'],)).fetchone()
        if not row:
            raise ItemPermissionError('单件关联不完整，请由李睿琪或曾悦处理')
        require_item_edit(conn, username, json.loads(row['base_json']), json.loads(row['override_json']), {}, workspace)
