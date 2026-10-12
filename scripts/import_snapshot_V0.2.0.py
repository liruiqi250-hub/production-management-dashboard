"""把经复核的业务快照幂等导入本机生产台账。"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.store import Store

parser = argparse.ArgumentParser()
parser.add_argument("snapshot", type=Path)
args = parser.parse_args()
snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
result = Store(ROOT).import_snapshot(snapshot)
print(json.dumps(result, ensure_ascii=True))
