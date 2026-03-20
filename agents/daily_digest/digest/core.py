"""digest 核心模块 — 配置加载、数据拉取、格式化

所有函数为纯函数或只读操作，不修改外部状态。
"""

from datetime import datetime, timedelta
from pathlib import Path

from .client import LibraClient, BASE_URL
from .experiment import ExperimentHelper
from .config import load_metrics3_config, COOKIES_PATH, LIBRA_FLIGHT_URL

DIGEST_DIR = Path(__file__).parent
FLIGHTS_API_PATH = "/datatester/experiment/api/v3/app/-1/experiment"

MAX_EXPERIMENT_AGE_DAYS = 14
RETRY_WAITS = [15, 30, 60]


# ========== 配置加载 ==========

def load_digest_config():
    """从 metrics3.json 加载 digest 指标配置

    返回 digest=true 的组，保留 display_mode 字段。
    """
    config = load_metrics3_config()
    groups = []
    for g in config.get("metric_groups", []):
        if not g.get("digest", False):
            continue
        metrics = []
        for m in g.get("metrics", []):
            metrics.append({
                "id": m["id"],
                "name": m["name"],
                "short": m.get("short"),
                "rule": m.get("digest_rule"),
                "display_mode": m.get("display_mode"),  # None = cumulative
            })
        groups.append({
            "group_id": g["group_id"],
            "group_name": g["group_name"],
            "metrics": metrics,
        })
    return {"metric_groups": groups}


# ========== 数据获取 ==========

def fetch_running_experiments(session):
    """获取所有运行中的实验（只读 GET）"""
    url = f"{BASE_URL}{FLIGHTS_API_PATH}"
    params = {
        "owner_type": "my",
        "page": 1,
        "page_size": 100,
        "search_type": "id",
        "status": 1,
    }
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if result.get('code') not in (0, 200):
        raise Exception(f"flights API 错误: {result.get('message')}")
    return result.get('data', {}).get('experiments', [])


def get_version_info(client, flight_id):
    """获取对照组 vid 和所有实验组信息

    Returns:
        (base_vid, base_vname, exp_versions)
        exp_versions = [(vid, vname), ...]
    """
    baseuser_data = client.get_baseuser(flight_id)
    info = ExperimentHelper.identify_base_version(baseuser_data.get('baseuser', []))
    exp_versions = [(vid, vname) for vid, vname, _ in info["exp_versions"]]
    return info["base_vid"], info["base_vname"], exp_versions


def get_date_range(start_time_ts):
    """自动计算实验的日期范围

    Returns:
        (start_date, end_date, valid)
    """
    end_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
    if start_time_ts:
        start_date = datetime.fromtimestamp(start_time_ts).strftime('%Y-%m-%d')
    else:
        start_date = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
    return start_date, end_date, start_date <= end_date


def filter_recent_experiments(experiments):
    """筛选开启时间 ≤ 14 天的实验"""
    cutoff = datetime.now() - timedelta(days=MAX_EXPERIMENT_AGE_DAYS)
    recent, skipped = [], 0
    for exp in experiments:
        start_time = exp.get('start_time')
        if start_time and datetime.fromtimestamp(start_time) < cutoff:
            skipped += 1
        else:
            recent.append(exp)
    return recent, skipped


# ========== 指标拉取 ==========

def _extract_metric_data(merge_data, mid_str, vid_str, base_vid_str):
    """从 merge_data 提取单个指标的 rel_diff 和 confidence"""
    versions_data = merge_data.get(mid_str)
    if not versions_data:
        return None, None, f'metric_id {mid_str} 不在 merge_data 中'
    if vid_str not in versions_data:
        return None, None, f'版本无数据'
    vid_data = versions_data[vid_str]
    rd = (vid_data.get('relative_diff') or {}).get(base_vid_str)
    cf = (vid_data.get('confidence') or {}).get(base_vid_str)
    if rd is None:
        return None, None, 'relative_diff 为 null'
    return rd, cf, None


def fetch_experiment_metrics(client, flight_id, config, base_vid, exp_versions,
                             start_date, end_date, data_region=None):
    """拉取一个实验的所有关注指标

    支持 per-metric display_mode：对包含 average 指标的组，
    额外调用 merge_type="average" API 并替换对应指标数据。

    Args:
        data_region: 地区筛选（"EU"/"ROW"/"US"），None=不传（全局数据）

    Returns:
        (versions_results, warnings, status)
    """
    base_vid_str = str(base_vid)
    warnings = []

    # 第一步：按指标组拉取数据
    groups_data = []
    hit_415 = False
    has_computing = False
    has_unavailable = False

    for group in config['metric_groups']:
        gid = group['group_id']
        gname = group['group_name']

        if hit_415:
            groups_data.append((group, None, None, "415"))
            continue

        # 检查是否有需要 average 的指标
        avg_metric_ids = {
            str(m["id"]) for m in group["metrics"]
            if m.get("display_mode") == "average"
        }

        try:
            lean_data = client.get_lean_data(
                flight_id, gid, start_date, end_date, base_vid,
                data_region=data_region,
            )
            data_status = lean_data.get('data_status')
            has_stats = lean_data.get('has_stats')

            if data_status == 2 and not has_stats:
                has_computing = True
                groups_data.append((group, lean_data, None, "computing"))
            elif data_status == 4 or (data_status != 1 and not has_stats):
                has_unavailable = True
                groups_data.append((group, lean_data, None, "unavailable"))
            else:
                avg_data = None
                if avg_metric_ids:
                    try:
                        avg_data = client.get_lean_data(
                            flight_id, gid, start_date, end_date,
                            base_vid, merge_type="average",
                            data_region=data_region,
                        )
                    except Exception as e:
                        warnings.append(f"指标组 [{gname}] average API 失败: {e}")
                groups_data.append((group, lean_data, avg_data, None))
        except Exception as e:
            if 'code=415' in str(e):
                hit_415 = True
                groups_data.append((group, None, None, "415"))
                warnings.append(f"实验数据尚未就绪（end_date={end_date}）")
            else:
                warnings.append(f"指标组 [{gname}] 请求失败: {e}")
                groups_data.append((group, None, None, "error"))

    # 第二步：对每个实验版本，提取该版本的指标数据
    versions_results = []
    for vid, vname in exp_versions:
        vid_str = str(vid)
        metrics_results = []

        for group_cfg, total_data, avg_data, error_tag in groups_data:
            gname = group_cfg['group_name']
            group_result = {'group_name': gname, 'metrics': []}

            avg_ids = {
                str(m["id"]) for m in group_cfg["metrics"]
                if m.get("display_mode") == "average"
            }

            if total_data is None:
                error_map = {"415": "日期越界(415)", "error": "请求失败"}
                for m in group_cfg['metrics']:
                    group_result['metrics'].append({
                        'metric_id': m["id"], 'name': m["name"],
                        'short': m["short"], 'rule': m["rule"],
                        'rel_diff': None, 'confidence': None,
                        'error': error_map.get(error_tag, "未知错误"),
                    })
                metrics_results.append(group_result)
                continue

            total_merge = total_data.get('merge_data', {})
            avg_merge = avg_data.get('merge_data', {}) if avg_data else {}

            for m in group_cfg['metrics']:
                mid_str = str(m["id"])
                use_avg = mid_str in avg_ids and avg_merge
                source = avg_merge if use_avg else total_merge

                rd, cf, err = _extract_metric_data(source, mid_str, vid_str, base_vid_str)

                group_result['metrics'].append({
                    'metric_id': m["id"], 'name': m["name"],
                    'short': m["short"], 'rule': m["rule"],
                    'rel_diff': rd, 'confidence': cf, 'error': err,
                })
            metrics_results.append(group_result)
        versions_results.append((vname, metrics_results))

    # 判断整体状态
    all_null = versions_results and all(
        all(mr['rel_diff'] is None for gr in mrs for mr in gr['metrics'])
        for _, mrs in versions_results
    )

    if hit_415:
        status = '415'
    elif all_null and has_computing:
        status = 'computing'
    elif all_null and has_unavailable:
        status = 'unavailable'
    elif all_null:
        status = 'all_null'
    else:
        status = 'ok'

    return versions_results, warnings, status


# ========== 核心查询接口 ==========

def query_experiment(client, flight_id, config, exp_name=None, start_time_ts=None,
                     start_date=None, end_date=None, data_region=None):
    """查询单个实验指标，返回结构化结果

    Args:
        client: LibraClient 实例
        flight_id: 实验 ID
        config: load_digest_config() 的返回值
        exp_name: 实验名称（None 则用默认名）
        start_time_ts: 实验开启时间戳（用于自动计算日期范围）
        start_date: 手动指定开始日期，覆盖自动计算
        end_date: 手动指定结束日期，覆盖自动计算
        data_region: 地区筛选（"EU"/"ROW"/"US"），None=全局数据

    Returns:
        {
            'name': str, 'flight_id': int,
            'start_date': str, 'end_date': str,
            'versions_results': list, 'warnings': list,
            'status': 'ok'|'computing'|'skip'|'unavailable'|'415'|'fail',
            'skip_reason': str | None,
            'error': str | None,
        }
    """
    if exp_name is None:
        exp_name = f'实验 {flight_id}'

    result = {
        'name': exp_name, 'flight_id': flight_id,
        'start_date': None, 'end_date': None,
        'versions_results': [], 'warnings': [],
        'status': 'fail', 'skip_reason': None, 'error': None,
    }

    try:
        base_vid, base_vname, exp_versions = get_version_info(client, flight_id)

        # 日期：手动指定优先，否则自动计算
        if start_date and end_date:
            auto_start, auto_end = start_date, end_date
            date_valid = start_date <= end_date
        else:
            auto_start, auto_end, date_valid = get_date_range(start_time_ts)
            # 允许部分覆盖
            if start_date:
                auto_start = start_date
                date_valid = auto_start <= auto_end
            if end_date:
                auto_end = end_date
                date_valid = auto_start <= auto_end

        result['start_date'] = auto_start
        result['end_date'] = auto_end

        if not date_valid:
            result['status'] = 'skip'
            result['skip_reason'] = f"日期范围无效 start={auto_start} > end={auto_end}"
            return result

        if not exp_versions:
            result['status'] = 'skip'
            result['skip_reason'] = f"无实验组版本（仅对照组 {base_vname}）"
            return result

        versions_results, warnings, status = fetch_experiment_metrics(
            client, flight_id, config, base_vid, exp_versions,
            auto_start, auto_end, data_region=data_region,
        )

        result['versions_results'] = versions_results
        result['warnings'] = warnings

        if status == '415':
            result['status'] = 'skip'
            result['skip_reason'] = f"API 415 (end_date={auto_end})"
        elif status == 'computing':
            result['status'] = 'computing'
            result['skip_reason'] = f"后端计算中 (data_status=2, {len(exp_versions)}个版本)"
        elif status in ('unavailable', 'all_null'):
            result['status'] = 'skip'
            result['skip_reason'] = (
                f"数据不可用 (data_status=4, {len(exp_versions)}个版本)"
                if status == 'unavailable' else "所有 rel_diff=null"
            )
        else:
            result['status'] = 'ok'

        return result

    except Exception as e:
        result['status'] = 'fail'
        result['error'] = str(e)
        return result


# ========== 格式化输出 ==========

def format_pct(rel_diff, confidence):
    """格式化百分比，带显著性 emoji"""
    if rel_diff is None:
        return "N/A"
    pct = rel_diff * 100
    text = f"{pct:+.2f}%"
    if confidence == 1:
        return f"🟢{text}"
    elif confidence == -1:
        return f"🔴{text}"
    return text


def build_summary_table(all_results, config):
    """构建顶部 Markdown 汇总表格

    Args:
        all_results: [query_experiment() 返回的 dict, ...] 或
                     [(exp_name, start_date, versions_results, flight_id), ...] 兼容旧格式
    """
    headers = [m["short"] for g in config['metric_groups'] for m in g['metrics'] if m["short"]]

    lines = [
        "| 实验 | 开启 | " + " | ".join(headers) + " |",
        "|---" * (2 + len(headers)) + "|",
    ]

    for item in all_results:
        # 兼容新旧格式
        if isinstance(item, dict):
            exp_name = item['name']
            start_date = item['start_date']
            versions_results = item['versions_results']
        else:
            exp_name, start_date, versions_results, _ = item

        multi = len(versions_results) > 1

        for vi, (vname, metrics_results) in enumerate(versions_results):
            if multi:
                label = f"{exp_name} ({vname})" if vi == 0 else f"  ({vname})"
                date_cell = start_date if vi == 0 else ""
            else:
                label = exp_name
                date_cell = start_date

            metric_map = {}
            for gr in metrics_results:
                for mr in gr['metrics']:
                    if mr['short']:
                        metric_map[mr['short']] = mr

            cells = [label, date_cell] + [
                format_pct(metric_map[h]['rel_diff'], metric_map[h]['confidence'])
                if h in metric_map and metric_map[h]['rel_diff'] is not None
                else "N/A"
                for h in headers
            ]
            lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def build_detail(exp_index, exp_name, flight_id, versions_results, warnings):
    """构建单个实验的详细输出"""
    lines = [
        f"{exp_index}. {exp_name}",
        f"   {LIBRA_FLIGHT_URL.format(flight_id=flight_id)}",
    ]
    multi = len(versions_results) > 1

    for vname, metrics_results in versions_results:
        if multi:
            lines += ["", f"   --- {vname} ---"]
        for gr in metrics_results:
            texts = [
                f"❓{mr['name']} {mr['error']}" if mr['error']
                else f"{mr['name']} {format_pct(mr['rel_diff'], mr['confidence'])}"
                for mr in gr['metrics']
            ]
            lines += [f"   [{gr['group_name']}]", f"   {', '.join(texts)}"]

    # 一句话总结
    summary_parts = []
    for vname, metrics_results in versions_results:
        parts = []
        for gr in metrics_results:
            for mr in gr['metrics']:
                if mr['rel_diff'] is None or not mr['short']:
                    continue
                pct_text = f"{mr['rel_diff'] * 100:+.2f}%"
                if mr['rule'] == 'always' or (mr['rule'] == 'optional' and mr['confidence'] in (1, -1)):
                    parts.append(f"{mr['short']} {pct_text}")
        if parts:
            summary_parts.append(f"{vname}: {', '.join(parts)}" if multi else ", ".join(parts))

    if summary_parts:
        sep = "; " if multi else ", "
        lines += ["", f"libra: {sep.join(summary_parts)}"]

    if warnings:
        lines += [""] + [f"   {w}" for w in warnings]

    return "\n".join(lines)
