"""可选HTTPS网关：仅本机显式配置的回环连接及专用密钥可声明外网来源。"""
import hmac
import ipaddress
import json
from urllib.parse import urlsplit


class RemoteGateway:
    def __init__(self, store):
        self.origin = None
        self.secret = None
        path = store.private / 'remote_access' / (store.path.stem + '.json')
        try:
            value = json.loads(path.read_text('utf-8-sig'))
            if value.get('enabled') is not True:
                return
            origin = value['public_origin']
            parsed = urlsplit(origin)
            secret = value['gateway_secret']
            import re
            if (parsed.scheme != 'https' or not parsed.hostname or '.' not in parsed.hostname
                    or parsed.username or parsed.password or parsed.port not in (None,443)
                    or parsed.path or parsed.query or parsed.fragment
                    or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',parsed.hostname)
                    or not isinstance(secret,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',secret)):
                return
            try:
                ipaddress.ip_address(parsed.hostname)
                return
            except ValueError:
                pass
            self.origin, self.secret = origin, secret
        except (OSError, ValueError, KeyError, TypeError):
            return

    def verify(self, client_ip, headers):
        if not self.origin or not ipaddress.ip_address(client_ip).is_loopback:
            return None
        key = headers.get('X-Dino-Gateway-Key','')
        claimed = headers.get('X-Dino-Public-Origin','')
        if not isinstance(key,str) or not key.isascii() or not hmac.compare_digest(key,self.secret) or claimed != self.origin:
            return None
        return self.origin
