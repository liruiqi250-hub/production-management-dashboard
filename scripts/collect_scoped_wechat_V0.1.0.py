"""历史采集入口兼容：统一使用当前限定群和滚动日期的实现。"""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("collect_scoped_wechat_V0.1.3.py")), run_name="__main__")
