"""每日实验摘要 — 一键拉取所有运行中实验的关注指标

用法:
    python -m digest.batch
"""

import sys
import time
from datetime import datetime

from .client import LibraClient
from .config import COOKIES_PATH
from .core import (
    DIGEST_DIR,
    RETRY_WAITS,
    load_digest_config,
    fetch_running_experiments,
    filter_recent_experiments,
    query_experiment,
    build_summary_table,
    build_detail,
)


def run_daily_digest():
    """每日摘要主函数"""
    if not COOKIES_PATH.exists():
        print(f"❌ cookies 文件不存在: {COOKIES_PATH}", file=sys.stderr)
        print("请先运行 get_cookies.py 获取 cookies", file=sys.stderr)
        return

    config = load_digest_config()
    client = LibraClient(COOKIES_PATH)

    # 获取运行中的实验
    print("正在获取运行中的实验列表...", file=sys.stderr)
    try:
        experiments = fetch_running_experiments(client.session)
    except Exception as e:
        print(f"❌ 获取实验列表失败: {e}", file=sys.stderr)
        if "401" in str(e) or "login" in str(e).lower():
            print("Cookie 可能已过期，请重新获取", file=sys.stderr)
        return

    print(f"共 {len(experiments)} 个运行中的实验", file=sys.stderr)
    if not experiments:
        print("没有运行中的实验")
        return

    recent_exps, skipped = filter_recent_experiments(experiments)
    print(f"筛选后: {len(recent_exps)} 个近期实验（跳过 {skipped} 个长期实验）", file=sys.stderr)
    if not recent_exps:
        print("没有 14 天内启动的实验")
        return

    # === 第一轮 ===
    ok_results = []
    not_ready = []
    failed = []
    retry_queue = []

    for i, exp in enumerate(recent_exps):
        label = f"[{i+1}/{len(recent_exps)}] "
        exp_name = exp.get('name', '未知实验')
        flight_id = exp['id']
        print(f"\n{label}{exp_name} (id={flight_id})", file=sys.stderr)

        r = query_experiment(client, flight_id, config, exp_name, exp.get('start_time'))

        if r['status'] == 'ok':
            ok_results.append(r)
            print(f"  ✅ 完成", file=sys.stderr)
        elif r['status'] == 'computing':
            retry_queue.append(exp)
            print(f"  ⏳ {r['skip_reason']}", file=sys.stderr)
        elif r['status'] == 'fail':
            failed.append((r['name'], r['flight_id'], r['error']))
            print(f"  ❌ {r['error']}", file=sys.stderr)
        else:
            not_ready.append((r['name'], r['flight_id'], r['skip_reason']))
            print(f"  ⏳ {r['skip_reason']}", file=sys.stderr)

    # === 重试 computing 的实验 ===
    if retry_queue:
        for attempt, wait in enumerate(RETRY_WAITS):
            print(f"\n{'='*40}", file=sys.stderr)
            print(f"⏳ {len(retry_queue)} 个实验后端计算中，等待 {wait}s 后重试 "
                  f"(第 {attempt+1}/{len(RETRY_WAITS)} 轮)...", file=sys.stderr)
            time.sleep(wait)

            still_computing = []
            for exp in retry_queue:
                exp_name = exp.get('name', '未知实验')
                flight_id = exp['id']
                r = query_experiment(client, flight_id, config, exp_name, exp.get('start_time'))

                if r['status'] == 'ok':
                    ok_results.append(r)
                    print(f"  ✅ {exp_name}", file=sys.stderr)
                elif r['status'] == 'computing':
                    still_computing.append(exp)
                    print(f"  ⏳ {exp_name} 仍在计算中", file=sys.stderr)
                elif r['status'] == 'fail':
                    failed.append((r['name'], r['flight_id'], r['error']))
                else:
                    not_ready.append((r['name'], r['flight_id'], r['skip_reason']))

            retry_queue = still_computing
            if not retry_queue:
                print("  ✅ 所有实验数据已就绪", file=sys.stderr)
                break
        else:
            for exp in retry_queue:
                not_ready.append((
                    exp.get('name', '未知实验'), exp['id'],
                    f"后端计算中，重试 {len(RETRY_WAITS)} 轮仍未就绪"
                ))

    # === 构建输出 ===
    today = datetime.now().strftime('%Y-%m-%d')

    header = [f"每日实验摘要 {today}"]
    stats = f"共 {len(ok_results)} 个实验有数据"
    if not_ready:
        stats += f"，{len(not_ready)} 个数据未就绪"
    if skipped:
        stats += f"，{skipped} 个长期实验已跳过"
    header += [stats, ""]
    if ok_results:
        header.append(build_summary_table(ok_results, config))

    issues = []
    if not_ready:
        issues += ["", "=" * 40, f"⏳ {len(not_ready)} 个实验数据尚未就绪:"]
        issues += [f"  - {n} (id={f}): {r}" for n, f, r in not_ready]
    if failed:
        issues += ["", "=" * 40, f"🚨 {len(failed)} 个实验拉取失败:"]
        issues += [f"  ❌ {n} (id={f}): {e}" for n, f, e in failed]

    details_lines = ["", "=" * 40]
    for i, r in enumerate(ok_results):
        detail = build_detail(i + 1, r['name'], r['flight_id'],
                              r['versions_results'], r['warnings'])
        details_lines.append(f"\n{detail}")

    short_output = "\n".join(header + issues)
    full_output = "\n".join(header + details_lines + issues)

    print(short_output)

    output_dir = DIGEST_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    for suffix, content in [("short", short_output), ("full", full_output)]:
        path = output_dir / f"digest_{today}_{suffix}.txt"
        path.write_text(content, encoding="utf-8")

    print(f"\n已保存到 {output_dir}/digest_{today}_{{short,full}}.txt", file=sys.stderr)


if __name__ == "__main__":
    try:
        run_daily_digest()
    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
