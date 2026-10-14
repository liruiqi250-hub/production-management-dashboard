"""邮箱附件待核对台账；自动提取只生成草稿，人工确认才进入业务资料。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import tempfile
import uuid

from app.lantern_attachment_reader import extract_document
from app.lantern_documents import FIELDS
from app.lantern_sources import _local_file
from app.lantern_store import PROJECT_FIELDS, MAX_UPLOAD_BYTES, RevisionConflict
from app.store import now


class EmailInbox:
    def __init__(self, store):
        self.store = store
        self.base = _local_file(store.workspace, 'private/lantern_inbox_files')
        self.base.mkdir(parents=True, exist_ok=True)
        with store.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS lantern_inbox(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,status TEXT NOT NULL,data_json TEXT NOT NULL,created_at TEXT NOT NULL)')

    def get(self, document_id):
        if not isinstance(document_id, str) or not re.fullmatch('[a-f0-9]{64}', document_id):
            raise KeyError('附件待核对记录不存在')
        with self.store.connect() as conn:
            row = conn.execute('SELECT data_json FROM lantern_inbox WHERE id=?', (document_id,)).fetchone()
        if not row:
            raise KeyError('附件待核对记录不存在')
        return json.loads(row[0])

    def listing(self, offset=0):
        if type(offset) is not int or offset < 0 or offset > 100000:
            raise ValueError('附件列表分页无效')
        with self.store.connect() as conn:
            total = conn.execute('SELECT count(*) FROM lantern_inbox').fetchone()[0]
            pending = conn.execute("SELECT count(*) FROM lantern_inbox WHERE status='pending'").fetchone()[0]
            rows = conn.execute("SELECT data_json FROM lantern_inbox ORDER BY status='pending' DESC,created_at DESC,id LIMIT 50 OFFSET ?", (offset,)).fetchall()
        keys = {'id', 'revision', 'status', 'kind', 'filename', 'bytes', 'source', 'sender', 'time', 'reviewed_by_name', 'reviewed_at', 'project_id', 'warnings'}
        return {'documents': [{key: value for key, value in json.loads(row[0]).items() if key in keys} for row in rows],
                'total': total, 'pending': pending, 'offset': offset, 'has_more': offset + len(rows) < total}

    def original(self, document_id):
        record = self.get(document_id)
        content = _local_file(self.base, document_id).read_bytes()
        if len(content) != record['bytes'] or hashlib.sha256(content).hexdigest() != record['sha256']:
            raise ValueError('附件原件校验失败，请保留记录并联系维护人员')
        return record, content

    def ingest(self, entry, job_folder, actor):
        allowed = {'filename', 'path', 'sha256', 'bytes', 'source_ref', 'source', 'sender', 'time', 'part'}
        if not isinstance(entry, dict) or set(entry) != allowed:
            raise ValueError('邮箱附件来源结构无效')
        content = _local_file(job_folder, entry['path']).read_bytes()
        if not 0 < len(content) <= MAX_UPLOAD_BYTES or len(content) != entry['bytes'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise ValueError('邮箱附件大小或SHA校验失败')
        key = '\n'.join(str(entry[x]) for x in ('source', 'source_ref', 'time', 'part', 'sha256'))
        document_id = hashlib.sha256(key.encode()).hexdigest()
        with self.store.connect() as conn:
            row = conn.execute('SELECT data_json FROM lantern_inbox WHERE id=?', (document_id,)).fetchone()
        if row:
            return json.loads(row[0]), False
        with self.store.connect() as conn:
            self.store._actor(conn, actor)
        with tempfile.TemporaryDirectory(dir=self.base, prefix='read_') as folder:
            try:
                parsed = extract_document(entry['filename'], content, folder)
            except Exception as exc:
                parsed = {'kind': 'unknown', 'fields': {}, 'candidates': {}, 'text': '',
                          'warnings': [str(exc) if isinstance(exc, ValueError) else '附件读取未完成，原件已保留，请对照填写或转换格式后重试。']}
        record = {key: entry[key] for key in entry if key != 'path'}
        record.update(parsed)
        record.update(id=document_id, status='pending', revision=1, created_at=now(), reviewed_at=None,
                      reviewed_by_name=None, attachment_id=None, project_id=None)
        destination = _local_file(self.base, document_id)
        destination.write_bytes(content)
        with self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO lantern_inbox VALUES(?,?,?,?,?)', (document_id, 1, 'pending', json.dumps(record, ensure_ascii=False), record['created_at']))
        return self.get(document_id), True

    def confirm(self, document_id, data, actor):
        if not isinstance(data, dict) or set(data) != {'revision', 'kind', 'fields', 'confirmed'} or data['confirmed'] is not True:
            raise ValueError('请先核对附件类型和提取字段，再明确确认')
        if type(data['revision']) is not int or data['kind'] not in {'production', 'pi', 'packing'}:
            raise ValueError('附件类型或版本号无效')
        fields = data['fields']
        if not isinstance(fields, dict):
            raise ValueError('核对字段需为对象')
        kind = data['kind']
        allowed = (set(PROJECT_FIELDS) - {'source_attachment'} | {'products'}) if kind == 'production' else FIELDS
        if set(fields) - allowed:
            raise ValueError('核对资料包含不支持的字段')
        if kind != 'production' and any(not isinstance(value, str) or len(value) > 4000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value) for value in fields.values()):
            raise ValueError('核对字段需为有限长度文字，不能包含控制字符')
        # 原件先验真；确认不会信任网页提供的附件路径或SHA。
        original, content = self.original(document_id)
        if original.get('status') == 'confirmed':
            raise RevisionConflict('此附件已确认，请刷新；重复采集不会重新建立项目或覆盖核对结果')
        created_path = None
        try:
            with self.store.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                user = self.store._actor(conn, actor)
                row = conn.execute('SELECT revision,data_json FROM lantern_inbox WHERE id=?', (document_id,)).fetchone()
                if row[0] != data['revision']:
                    raise RevisionConflict('附件已被其他同事核对，请刷新后再处理')
                record = json.loads(row[1])
                if record['status'] == 'confirmed':
                    raise RevisionConflict('附件已确认，未重复创建项目')
                stamp = now()
                project = None
                if kind == 'production':
                    products = fields.get('products', [])
                    if not isinstance(products, list) or any(not isinstance(product, dict) or set(product)-{'name','specification','quantity'} for product in products):
                        raise ValueError('生产单产品仅支持名称、规格、数量，工序需在项目里另行核对')
                    project = self.store._project(fields, {}, conn)
                attachment_id = record.get('attachment_id')
                filename = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', original['filename']).strip(' .')[:140] or '附件'
                suffix = Path(filename).suffix.casefold()
                if not attachment_id and suffix in {'.pdf','.doc','.docx','.xls','.xlsx','.csv','.txt','.png','.jpg','.jpeg'}:
                    attachment_id = uuid.uuid4().hex
                    created_path = _local_file(self.store.uploads, attachment_id + suffix)
                    self.store.uploads.mkdir(parents=True, exist_ok=True)
                    with created_path.open('xb') as handle:
                        handle.write(content)
                    conn.execute('INSERT INTO lantern_attachments VALUES(?,?,?,?,?,?,?)', (attachment_id, filename, suffix, len(content), original['sha256'], stamp, user['username']))
                if project is not None:
                    project['source_attachment'] = attachment_id or ''
                    project.update(id=uuid.uuid4().hex, revision=1, created_at=stamp, updated_at=stamp,
                                   updated_by=user['username'], updated_by_name=user['display_name'])
                    conn.execute('INSERT INTO lantern_projects VALUES(?,?,?,?)', (project['id'], json.dumps(project, ensure_ascii=False), 1, stamp))
                    record['project_id'] = project['id']
                record.update(kind=kind, fields=fields, status='confirmed', revision=record['revision']+1,
                              reviewed_at=stamp, reviewed_by_name=user['display_name'], attachment_id=attachment_id)
                conn.execute('UPDATE lantern_inbox SET revision=?,status=?,data_json=? WHERE id=?', (record['revision'], record['status'], json.dumps(record, ensure_ascii=False), document_id))
                conn.execute('INSERT INTO audit(time,username,action,item_id,details_json) VALUES(?,?,?,?,?)',
                             (stamp, user['username'], 'confirm_email_document', document_id, json.dumps({'kind':kind,'source_ref':record['source_ref'],'sha256':record['sha256'],'project_id':record['project_id']},ensure_ascii=False)))
        except Exception:
            if created_path is not None:
                created_path.unlink(missing_ok=True)
            raise

        return record


def ingest_documents(store, documents, job_folder, actor):
    if not isinstance(documents, list) or len(documents) > 2000:
        raise ValueError('邮箱附件数量或结构无效')
    inbox = EmailInbox(store)
    messages, limits = [], []
    budget = 240000
    for entry in documents:
        record, _ = inbox.ingest(entry, job_folder, actor)
        reviewed = ''
        if record['status'] == 'confirmed':
            reviewed = '[人工已核对字段；核对人：' + record['reviewed_by_name'] + '；时间：' + record['reviewed_at'] + ']\n' + json.dumps(record['fields'], ensure_ascii=False) + '\n'
        full_text = reviewed + ('[原件自动提取文字，可能含识别误差]\n' + record['text'] if record['text'] else '')
        text = full_text[:min(40000, budget)]
        if text:
            messages.append({'ref': 'email-attachment:' + record['id'], 'source': record['source'] + '/附件:' + record['filename'],
                             'sender': record['sender'], 'time': record['time'],
                             'text': ('[附件部分字段已经人工核对]\n' if reviewed else '[附件提取文字，未经人工核对]\n') + text})
            budget -= len(text)
        limits.extend(record['warnings'])
        if len(text) < len(full_text):
            limits.append('附件分析输入达到单文件或本轮文字上限，部分文字未交给Codex；原件及核对记录保留，请对照核对。')
    if documents:
        limits.append('邮箱附件已生成待核对资料；未经人工确认，不创建生产项目，不用于正式报关资料。')
    return messages, list(dict.fromkeys(limits))
