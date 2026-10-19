"""计划任务专用入口：无控制台，本机留档，不打印收件身份与正文。"""
from pathlib import Path
import sys
import json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.store import Store, now
from app.jobs import Jobs
from app.notification_cards import check_failure
try:
    result = check_failure(Store(ROOT),Jobs(ROOT).status(),deadline=True)
except Exception:
    result = {'state':'failed','message':'本人定时提醒检查未完成，请核对本机钉钉配置、网络和账号绑定'}
folder = ROOT / 'private/notifications';folder.mkdir(parents=True,exist_ok=True)
(folder/'deadline_check.json').write_text(json.dumps({'checked_at':now(),**result},ensure_ascii=False,indent=2),'utf-8')
