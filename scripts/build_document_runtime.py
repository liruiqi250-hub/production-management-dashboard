"""安装锁定的纯Python旧Excel读取组件；不打包本机环境。"""
import hashlib
import json
from pathlib import Path
import stat
import urllib.parse
import urllib.request
import zipfile

try:
    from scripts.publish_github import _safe_path, _relative_name
except ModuleNotFoundError:
    from publish_github import _safe_path, _relative_name


def build_document_runtime(root, cache, source, *, offline=False):
    lock = json.loads((Path(source) / 'dependencies/lantern-documents.lock.json').read_text('utf-8-sig'))
    if lock.get('schema') != 1 or {item.get('id') for item in lock.get('components', [])} != {'xlrd', 'pypdf'} or len(lock['components']) != 2:
        raise ValueError('文档读取组件锁文件无效')
    for item in lock['components']:
        url = urllib.parse.urlsplit(item['url'])
        if item.get('id') not in {'xlrd', 'pypdf'} or url.scheme != 'https' or url.hostname != 'files.pythonhosted.org' or url.username:
            raise ValueError('报关资料组件来源不受支持')
        archive = _safe_path(Path(cache), item['archive_filename'])
        if not archive.exists():
            if offline:
                raise ValueError('缺少文档读取组件缓存')
            archive.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(item['url'], timeout=60) as response:
                if urllib.parse.urlsplit(response.geturl()).hostname != 'files.pythonhosted.org':
                    raise ValueError('组件下载被重定向至非官方来源')
                data = response.read(item['archive_bytes'] + 1)
            if len(data) != item['archive_bytes'] or hashlib.sha256(data).hexdigest() != item['archive_sha256']:
                raise ValueError('报关资料组件下载校验失败')
            archive.write_bytes(data)
        data = archive.read_bytes()
        if len(data) != item['archive_bytes'] or hashlib.sha256(data).hexdigest() != item['archive_sha256']:
            raise ValueError('报关资料组件缓存校验失败')
        target = _safe_path(Path(root), 'vendor/python/site-packages')
        with zipfile.ZipFile(archive) as package:
            if hashlib.sha256(package.read(item['license_path'])).hexdigest() != item['license_sha256']:
                raise ValueError('文档读取组件许可证校验失败')
            for entry in package.infolist():
                name = _relative_name(entry.filename)
                if entry.is_dir():
                    continue
                if not name.startswith((item['id']+'/', item['id']+'-'+item['version']+'.dist-info/', item['id']+'-'+item['version']+'.data/')) or name.endswith('.pth') or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('文档读取组件含未授权路径')
                destination = _safe_path(target, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(package.read(entry))
    pth = Path(root) / 'vendor/python/python313._pth'
    lines = pth.read_text('utf-8').splitlines()
    if 'site-packages' not in lines:
        lines.append('site-packages')
    pth.write_text('\n'.join(lines) + '\n', encoding='utf-8')

