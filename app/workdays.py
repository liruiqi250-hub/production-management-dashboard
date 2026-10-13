"""按已核验的中国调休日历计算完整工作日；未知年份不猜测。"""
from datetime import date, datetime, timedelta

CALENDAR_SOURCE = "https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html"
HOLIDAYS = set()
for start, end in [("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"),
                   ("2026-04-04", "2026-04-06"), ("2026-05-01", "2026-05-05"),
                   ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
                   ("2026-10-01", "2026-10-07")]:
    current = date.fromisoformat(start)
    while current <= date.fromisoformat(end):
        HOLIDAYS.add(current)
        current += timedelta(days=1)
WORKING_WEEKENDS = {date.fromisoformat(s) for s in
                    ["2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"]}


def is_workday(day):
    if day.year != 2026:
        raise ValueError("该年份节假日日历待补充")
    # 用户确认工厂平时周末也上班，仅扣国家公布的放假日期。
    return day not in HOLIDAYS


def priority(item, at=None):
    from app.store import TZ
    at = at or datetime.now(TZ)
    anchor = item.get("progress_at") or item.get("tracking_started_at") or item.get("first_seen")
    result = {"urgent": False, "workdays": None, "since": anchor,
              "basis": "连续3个完整工厂工作日无实质处理记录；周末计入，仅扣国家放假日期"}
    if item.get("status") in {"已解决", "完成验收"}:
        result["basis"] = "已完成，停止计时"
        return result
    if item.get("include_in_total") is False or item.get("status") == "待并案":
        result["basis"] = "待并案/历史参考，不计优先队列"
        return result
    try:
        start = datetime.fromisoformat(anchor).replace(tzinfo=TZ) if len(anchor) == 10 else datetime.fromisoformat(anchor).astimezone(TZ)
        if start > at:
            raise ValueError("处理时间晚于当前时间，待核对")
        count = 0
        current = start.date() + timedelta(days=1)
        # 即使当期尚不足三天，跨未配置年份也必须显式报告。
        is_workday(start.date())
        while current <= at.date():
            if is_workday(current) and (current < at.date() or at.timetz().replace(tzinfo=None) >= start.timetz().replace(tzinfo=None)):
                count += 1
            current += timedelta(days=1)
        result.update(workdays=count, urgent=count >= 3)
    except (TypeError, ValueError, AttributeError):
        result["basis"] = "日期或节假日日历待核对，暂不自动升级"
    return result
