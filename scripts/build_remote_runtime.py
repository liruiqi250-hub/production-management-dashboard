"""按固定来源、哈希与许可打包通用Cloudflare连接器；不包含任何隧道凭据。"""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request


def build_remote_runtime(root,cache,*,source,offline=False):
    source,root,cache=Path(source),Path(root),Path(cache)
    value=json.loads((source/'dependencies/cloudflared.lock.json').read_text('utf-8'))
    expected=f"https://github.com/cloudflare/cloudflared/releases/download/{value['version']}/cloudflared-windows-amd64.exe"
    if value['url']!=expected or value['filename']!='cloudflared-windows-amd64.exe':
        raise RuntimeError('连接器来源不符合固定发布规则')
    license_data=(source/'dependencies/cloudflared/LICENSE.txt').read_bytes()
    if hashlib.sha256(license_data).hexdigest()!=value['license_sha256']:
        raise RuntimeError('连接器许可文件校验失败')
    cache.mkdir(parents=True,exist_ok=True)
    binary=cache/value['filename']
    if not binary.exists():
        if offline:raise RuntimeError('离线缓存缺少连接器')
        with urllib.request.urlopen(expected,timeout=60) as response:binary.write_bytes(response.read())
    if binary.stat().st_size!=value['bytes'] or hashlib.sha256(binary.read_bytes()).hexdigest()!=value['sha256']:
        raise RuntimeError('连接器二进制校验失败')
    target=root/'vendor/cloudflared';target.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(binary,target/'cloudflared.exe')
    (target/'LICENSE.txt').write_bytes(license_data)
    (target/'SOURCE.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n','utf-8')
    return value
