"""指标数据爬虫 — 从 Libra API 获取指标数据并保存为 JSON

截图和爬虫数据必须对齐一致：两者应使用相同的日期范围参数。

用法:
    # 自动计算日期范围
    python crawl_metrics.py --flight_id 71732795 --version v9 --output output_doc

    # 手动指定日期范围（和截图对齐）
    python crawl_metrics.py --flight_id 71732795 --version v9 --output output_doc \
        --start_date 2026-02-03 --end_date 2026-02-17

输出: {output}/metrics_data.json
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from .libra_sdk.client import LibraClient
from .libra_sdk.experiment import ExperimentHelper
from .config import load_metrics3_config


def crawl(flight_id, target_version, output_dir, start_date=None, end_date=None, cookies_path=None):
    """从 Libra API 获取指标数据

    Returns:
        dict: 完整的指标数据，同时保存为 metrics_data.json
    """
    client = LibraClient(cookies_path)
    config = load_metrics3_config()

    # 1. 实验基本信息
    print("获取实验基本信息...")
    meta = client.get_conclusion_report_meta(flight_id)
    exp_name = meta.get("experiment_name", "")

    # 2. 版本信息
    print("获取版本信息...")
    baseuser_data = client.get_baseuser(flight_id)
    version_info = ExperimentHelper.identify_base_version(
        baseuser_data.get("baseuser", [])
    )

    # 找目标实验组
    target_vid = None
    target_users = 0
    for vid, vname, users in version_info["exp_versions"]:
        if vname == target_version:
            target_vid = vid
            target_users = users
            break
    if target_vid is None:
        available = [vname for _, vname, _ in version_info["exp_versions"]]
        raise ValueError(f"未找到实验组 {target_version}，可用: {available}")

    # 3. 日期范围
    if start_date and end_date:
        print(f"  使用手动指定的日期范围: {start_date} ~ {end_date}")
    else:
        start_date, end_date, valid = ExperimentHelper.compute_date_range(
            baseuser_data, meta
        )
        if not valid:
            print(f"  ⚠ 日期范围无效: {start_date} ~ {end_date}")

    print(f"  对照组 {version_info['base_vname']} (vid={version_info['base_vid']}): {version_info['base_users']:,}")
    print(f"  实验组 {target_version} (vid={target_vid}): {target_users:,}")
    print(f"  日期范围: {start_date} ~ {end_date}")

    # 4. 遍历指标组获取数据
    config_groups = config["metric_groups"]
    print(f"\n获取 {len(config_groups)} 个指标组数据...")
    groups_result = {}

    for i, group_cfg in enumerate(config_groups):
        gid = group_cfg["group_id"]
        gname = group_cfg["group_name"]
        try:
            lean_data = client.get_lean_data(
                flight_id, gid, start_date, end_date,
                version_info["base_vid"]
            )

            # 构建 name_map 和 display_mode 映射
            name_map = {}
            configured_ids = set()
            avg_metric_ids = set()
            for m in group_cfg.get("metrics", []):
                if m.get("id"):
                    mid_str = str(m["id"])
                    name_map[mid_str] = m["name"]
                    configured_ids.add(mid_str)
                    if m.get("display_mode") == "average":
                        avg_metric_ids.add(mid_str)

            fallback_names = lean_data.get("metric_name", {})
            all_metrics = ExperimentHelper.parse_metrics(
                lean_data.get("merge_data", {}),
                str(version_info["base_vid"]),
                str(target_vid),
                name_map=name_map,
                fallback_names=fallback_names,
            )

            # 有需要 average 的指标 → 额外调 API 并替换对应指标数据
            if avg_metric_ids:
                avg_data = client.get_lean_data(
                    flight_id, gid, start_date, end_date,
                    version_info["base_vid"], merge_type="average"
                )
                avg_metrics = ExperimentHelper.parse_metrics(
                    avg_data.get("merge_data", {}),
                    str(version_info["base_vid"]),
                    str(target_vid),
                    name_map=name_map,
                    fallback_names=avg_data.get("metric_name", {}),
                )
                avg_by_id = {m["metric_id"]: m for m in avg_metrics}
                all_metrics = [
                    avg_by_id[m["metric_id"]] if m["metric_id"] in avg_metric_ids and m["metric_id"] in avg_by_id
                    else m
                    for m in all_metrics
                ]

            # 只保留 config 中定义的指标
            if configured_ids:
                metrics = [m for m in all_metrics if m["metric_id"] in configured_ids]
            else:
                metrics = all_metrics

            sig_count = sum(1 for m in metrics if m["significant"])
            status = "显著" if sig_count > 0 else "Flat"
            print(f"  [{i+1}/{len(config_groups)}] {gname}: {len(metrics)} 指标, {sig_count} 显著 ({status})")

            groups_result[str(gid)] = {
                "group_name": gname,
                "section": group_cfg.get("section", "target"),
                "metrics": metrics,
            }
        except Exception as e:
            print(f"  [{i+1}/{len(config_groups)}] {gname}: 获取失败 - {e}")
            groups_result[str(gid)] = {
                "group_name": gname,
                "section": group_cfg.get("section", "target"),
                "metrics": [],
                "error": str(e),
            }

    # 5. 组装结果
    result = {
        "flight_id": flight_id,
        "experiment_name": exp_name,
        "base_vid": version_info["base_vid"],
        "base_vname": version_info["base_vname"],
        "base_users": version_info["base_users"],
        "target_vid": target_vid,
        "target_vname": target_version,
        "target_users": target_users,
        "start_date": start_date,
        "end_date": end_date,
        "crawl_time": datetime.now().isoformat(),
        "groups": groups_result,
    }

    # 6. 保存
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "metrics_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n数据已保存到 {json_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description="从 Libra API 获取指标数据")
    parser.add_argument("--flight_id", type=int, required=True, help="Libra 实验 ID")
    parser.add_argument("--version", type=str, required=True, help="目标实验组 (如 v9)")
    parser.add_argument("--output", type=str, default=str(Path(__file__).parent / "output"), help="输出目录（默认包内 output/）")
    parser.add_argument("--start_date", type=str, default=None, help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end_date", type=str, default=None, help="结束日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    crawl(
        flight_id=args.flight_id,
        target_version=args.version,
        output_dir=args.output,
        start_date=args.start_date,
        end_date=args.end_date,
    )


if __name__ == "__main__":
    main()
