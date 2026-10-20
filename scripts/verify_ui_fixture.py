"""启动全部虚构的 UI 验收服务，不读取或修改项目真实 private 数据库。

静态页面和业务代码使用当前源码；Store、账号、台账和日志只在独立的
TemporaryDirectory 中生成。端口仅绑定 127.0.0.1，按 Ctrl+C 关闭并清理。
脚本中公开的固定密码仅用于本次临时合成账号，不是正式账号凭据。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading


SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from app.server import DashboardServer
from app.store import Store, password_hash
from tests.test_store_server import synthetic_snapshot


SYNTHETIC_PASSWORD = "Synthetic-Pass-123"


def seed_synthetic_store(store: Store, server: DashboardServer) -> dict:
    """所有名称、原文、来源和进度均为验收虚构材料，不能用于实际生产。"""
    snapshot = synthetic_snapshot()
    original = snapshot["items"][0]
    snapshot["items"] = []
    for ident, title, owner in (
        ("SYNTHETIC-001", "虚构验收：机械图处理说明", "张英"),
        ("SYNTHETIC-002", "虚构验收：表皮返工跟进", "张英"),
        ("SYNTHETIC-003", "虚构验收：他人跟进事项", "郭逸"),
    ):
        item = copy.deepcopy(original)
        item.update(id=ident, title=title, owner=owner,
                    summary="全部为虚构 UI 验收资料。", next_step="虚构：补录实际检查说明")
        item["evidence"][0].update(sender="虚构测试人员", text="虚构 UI 验收原文：" + title)
        snapshot["items"].append(item)
    store.import_snapshot(snapshot)

    store.create_user("test_editor", "张英", "editor")
    store.create_user("test_viewer", "虚构查看人员", "viewer")
    with store.connect() as connection:
        # bootstrap_users 也只作用于临时库；不读取真实账号或其密码。
        for username in ("liruiqi", "zengyue", "test_editor", "test_viewer"):
            salt = connection.execute("SELECT salt FROM users WHERE username=?", (username,)).fetchone()[0]
            connection.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE username=?",
                               (password_hash(SYNTHETIC_PASSWORD, salt), username))

    ledger = {"version": "V1.0.0", "period": "2026-10", "cases": [
        {"id": "SYNTHETIC-SALES-001", "company": "大洋", "include_in_total": True,
         "status": "待检查", "owner": "张英", "observed": "虚构：验收设备动作需检查",
         "next_action": "虚构：核对设备动作并填写处理说明",
         "evidence": [{"source_id": "dayang", "source_ref": "synthetic-ui-fixture:1",
                       "sender": "虚构测试人员", "time": "2026-10-20T10:00:00+08:00",
                       "text": "全部为虚构售后验收原文。"}]}]}
    server.after_sales.import_ledger(ledger, "0" * 64)

    production = server.production
    order_id = production.add_order({"title": "虚构 UI 验收生产单", "order_no": "SYNTHETIC-UI-001",
                                     "business_contact": "虚构业务人员", "received_on": "2026-10-20",
                                     "source_ref": "synthetic-ui-fixture:order"}, "liruiqi")["id"]
    piece_ids = []
    for index, item_id in enumerate(("SYNTHETIC-001", "SYNTHETIC-002"), start=1):
        piece_id = production.add_piece({"order_id": order_id, "piece_label": f"虚构第 {index} 件",
                                         "product_name": f"虚构验收恐龙 {index}",
                                         "source_ref": f"synthetic-ui-fixture:piece:{index}"}, "liruiqi")["id"]
        piece_ids.append(piece_id)
        production.link(piece_id, {"item_type": "production", "item_id": item_id}, "liruiqi")
    production.update_stage(piece_ids[0], {"stage": "机械图", "state": "待业务确认", "basis": "人工核对",
                                          "note": "虚构：待业务核对机械图", "source_ref": "synthetic-ui-fixture:stage:1"}, "liruiqi")
    production.update_stage(piece_ids[0], {"stage": "钳工（机械、钢架）", "state": "返工", "basis": "厂内检查",
                                          "note": "虚构：钢架间隙需返工", "source_ref": "synthetic-ui-fixture:stage:2"}, "liruiqi")
    production.update_stage(piece_ids[1], {"stage": "机械图", "state": "合格", "basis": "人工核对",
                                          "note": "虚构：机械图复核合格", "source_ref": "synthetic-ui-fixture:stage:3"}, "liruiqi")
    production.update_stage(piece_ids[1], {"stage": "裱糊（纹理、表皮）", "state": "返工", "basis": "厂内检查",
                                          "note": "虚构：表皮纹理需返工", "source_ref": "synthetic-ui-fixture:stage:4"}, "liruiqi")
    production.update_stage(piece_ids[1], {"stage": "上色（喷色）", "state": "待业务确认", "basis": "人工核对",
                                          "note": "虚构：待业务核对配色", "source_ref": "synthetic-ui-fixture:stage:5"}, "liruiqi")
    return {"order_id": order_id, "piece_ids": piece_ids,
            "item_ids": [item["id"] for item in snapshot["items"]],
            "after_sales_id": "2026-10:大洋:SYNTHETIC-SALES-001"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8878, help="仅本机监听的隔离端口（默认 8878；0 为系统分配）")
    parser.add_argument("--workspace", action="store_true", help="输出新建的临时工作区路径，便于针对虚构库模拟并发变化")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 至 65535 之间")

    with tempfile.TemporaryDirectory(prefix="production-ui-synthetic-") as temporary:
        store = Store(temporary)
        server = DashboardServer(("127.0.0.1", args.port), store, "127.0.0.1")
        server.workspace_id = "synthetic-ui-fixture-only"
        try:
            metadata = seed_synthetic_store(store, server)
            # 仅保存合成对象编号，供另一个验收进程定位；没有真实业务资料。
            Path(temporary, "synthetic-fixture.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"虚构 UI 验收服务：http://127.0.0.1:{server.server_port}/；PID={os.getpid()}", flush=True)
            if args.workspace:
                print(f"临时工作区：{temporary}", flush=True)
            print("仅使用合成资料；按 Ctrl+C 停止后自动清理。", flush=True)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            worker.start()
            try:
                while worker.is_alive():
                    worker.join(timeout=0.5)
            except KeyboardInterrupt:
                pass
            finally:
                server.shutdown()
                worker.join(timeout=5)
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
