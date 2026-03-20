"""digest 模块 — 实验指标摘要

提供单实验查询和全实验每日摘要两个入口，共享核心数据拉取和格式化逻辑。
"""

from .config import COOKIES_PATH, LIBRA_FLIGHT_URL
from .client import LibraClient
from .core import (
    query_experiment,
    load_digest_config,
    fetch_running_experiments,
    filter_recent_experiments,
    get_version_info,
    get_date_range,
    build_detail,
    build_summary_table,
    format_pct,
)
def query_single_experiment(*args, **kwargs):
    from .single import query_single_experiment as _fn
    return _fn(*args, **kwargs)

def run_daily_digest(*args, **kwargs):
    from .batch import run_daily_digest as _fn
    return _fn(*args, **kwargs)
