"""导入已验收的本机售后JSON；不读取微信，不修改账号或人工结果。"""
import argparse
import hashlib
import json
from pathlib import Path
from app.after_sales import AfterSales
from app.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    validation = json.loads(args.validation.read_text("utf-8-sig"))
    data = json.loads(args.ledger.read_text("utf-8-sig"))
    if validation.get("valid") is not True or validation.get("error_count") != 0 or validation.get("period") != data.get("period"):
        raise ValueError("售后台账验收未通过或月份不符")
    result = AfterSales(Store(args.workspace)).import_ledger(data, hashlib.sha256(args.ledger.read_bytes()).hexdigest())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
