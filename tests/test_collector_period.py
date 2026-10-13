"""滚动日期授权边界检查，不读取微信或账号绑定。"""

import importlib.util
from datetime import datetime
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("scoped_collector", Path(__file__).resolve().parents[1] / "scripts" / "collect_scoped_wechat_V0.1.3.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class CollectorPeriodTests(unittest.TestCase):
    def test_tomorrow_uses_new_seven_day_window(self):
        now = datetime(2026, 10, 13, 15, 30, tzinfo=collector.TZ)
        start, end = collector.collection_period(None, None, now)
        self.assertEqual(start.isoformat(), "2026-10-07T00:00:00+08:00")
        self.assertEqual(end, now)
        self.assertEqual(collector.collection_period("2026-10-07", "2026-10-14", now), (start, end))

    def test_old_or_future_dates_cannot_expand_authorization(self):
        now = datetime(2026, 10, 13, 15, 30, tzinfo=collector.TZ)
        for start, end in [("2026-10-06", "2026-10-14"), ("2026-10-07", "2026-10-15"), ("2026-10-14", "2026-10-14")]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                collector.collection_period(start, end, now)

    def test_subset_and_cross_month_are_limited_to_actual_cutoff(self):
        now = datetime(2026, 11, 2, 10, 0, tzinfo=collector.TZ)
        start, end = collector.collection_period(None, None, now)
        self.assertEqual(start.isoformat(), "2026-10-27T00:00:00+08:00")
        self.assertEqual(end, now)
        start, end = collector.collection_period("2026-10-30", "2026-11-01", now)
        self.assertEqual(start.day, 30)
        self.assertEqual(end.isoformat(), "2026-11-01T00:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
