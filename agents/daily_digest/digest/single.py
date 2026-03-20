"""单实验指标查询 — 给定 flight_id，拉取并展示其所有关注指标

用法:
    python -m digest.single <flight_id>
    python -m digest.single 71787136 --start_date 2026-03-01 --end_date 2026-03-15
    python -m digest.single 71787136 --region US
"""

import argparse
import sys

from .client import LibraClient
from .config import COOKIES_PATH
from .core import (
    load_digest_config,
    query_experiment,
    build_detail,
)


def query_single_experiment(flight_id, start_date=None, end_date=None, data_region=None):
    """查询单个实验的所有关注指标

    Args:
        flight_id: 实验 ID
        start_date: 自定义开始日期（如 "2026-03-01"），None=自动计算
        end_date: 自定义结束日期（如 "2026-03-15"），None=自动计算
        data_region: 地区筛选（"EU"/"ROW"/"US"），None=全局数据
    """
    if not COOKIES_PATH.exists():
        print(f"❌ cookies 文件不存在: {COOKIES_PATH}", file=sys.stderr)
        print("请先运行 get_cookies.py 获取 cookies", file=sys.stderr)
        return

    client = LibraClient(COOKIES_PATH)
    config = load_digest_config()

    # 通过 meta API 获取实验名称和时间
    print(f"正在查询实验 {flight_id} ...", file=sys.stderr)
    try:
        meta = client.get_conclusion_report_meta(flight_id)
        exp_name = meta.get('experiment_name', f'实验 {flight_id}')
        start_time_ts = meta.get('start_time')
    except Exception as e:
        print(f"⚠️ 获取实验元信息失败: {e}", file=sys.stderr)
        exp_name = f'实验 {flight_id}'
        start_time_ts = None

    result = query_experiment(
        client, flight_id, config, exp_name, start_time_ts,
        start_date=start_date, end_date=end_date, data_region=data_region,
    )

    # 打印状态
    print(f"  日期: {result['start_date']} ~ {result['end_date']}", file=sys.stderr)
    if data_region:
        print(f"  地区: {data_region}", file=sys.stderr)

    if result['status'] == 'computing':
        print(f"⏳ {result['skip_reason']}", file=sys.stderr)
    elif result['status'] == 'skip':
        print(f"⚠️ {result['skip_reason']}", file=sys.stderr)
    elif result['status'] == 'fail':
        print(f"❌ {result['error']}", file=sys.stderr)
        return

    # 输出详情
    if result['versions_results']:
        detail = build_detail(1, result['name'], flight_id,
                              result['versions_results'], result['warnings'])
        print(detail)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="单实验指标查询")
    parser.add_argument("flight_id", type=int, help="实验 flight_id")
    parser.add_argument("--start_date", help="开始日期（如 2026-03-01），覆盖自动计算")
    parser.add_argument("--end_date", help="结束日期（如 2026-03-15），覆盖自动计算")
    parser.add_argument("--region", dest="data_region", choices=["EU", "ROW", "US"],
                        help="地区筛选（EU/ROW/US），不传则为全局数据")
    args = parser.parse_args()

    try:
        query_single_experiment(args.flight_id, args.start_date, args.end_date, args.data_region)
    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
