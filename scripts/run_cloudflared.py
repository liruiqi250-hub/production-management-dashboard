"""由随包pythonw运行固定连接器，凭据仅从本机token文件读取，不开控制台。"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0,str(ROOT))
    from app.store import Store
    from app.remote_access import RemoteGateway
    store=Store(ROOT)
    if not RemoteGateway(store).origin:
        return 2
    token=store.private/'remote_access/cloudflared.token'
    binary=ROOT/'vendor/cloudflared/cloudflared.exe'
    lock=json.loads((ROOT/'dependencies/cloudflared.lock.json').read_text('utf-8'))
    if (not token.is_file() or not binary.is_file()
            or hashlib.sha256(binary.read_bytes()).hexdigest()!=lock['sha256']):
        return 2
    # 单个计划任务IgnoreNew，锁另防止重复手动启动；进程退出即释放。
    import msvcrt
    with (token.parent/'connector.lock').open('a+b') as handle:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return 0
        log=token.parent/'connector.log'
        if log.exists() and log.stat().st_size>2*1024*1024:
            log.replace(log.with_suffix('.previous.log'))
        process=subprocess.Popen([str(binary),'--no-autoupdate','tunnel','--protocol','quic','--metrics','127.0.0.1:20242','--loglevel','warn','--logfile',str(log),'run','--token-file',str(token)],
                                 cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (token.parent/'connector.pid').write_text(str(process.pid),'ascii')
        return process.wait()


if __name__=='__main__':
    raise SystemExit(main())
