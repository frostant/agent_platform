"""Launch Report 生成器 — 一键从 Libra 实验生成飞书上线报告

用法:
    # 写入已有文档（测试）
    python generate_report.py --flight_id 71732795 --version v9 \
        --screenshots output/LYT0.01 \
        --doc_id LHQxdiSJAo7zJXxjw2pl28yqgsf

    # 创建新文档
    python generate_report.py --flight_id 71732795 --version v9 \
        --screenshots output/LYT0.01

    # 只查看数据摘要（不写飞书）
    python generate_report.py --flight_id 71732795 --version v9 --dry-run
"""

import argparse
import os
from pathlib import Path

# 加载 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from .report.generator import ReportGenerator
from .feishu_sdk import FeishuDoc


def main():
    parser = argparse.ArgumentParser(description="生成飞书上线报告")
    parser.add_argument("--flight_id", type=int, required=True, help="Libra 实验 ID")
    parser.add_argument("--version", type=str, required=True, help="目标实验组 (如 v9)")
    parser.add_argument("--screenshots", type=str, default=str(Path(__file__).parent / "output"), help="截图目录路径（默认包内 output/）")
    parser.add_argument("--doc_id", type=str, default=None, help="飞书文档 ID（不指定则创建新文档）")
    parser.add_argument("--dry-run", action="store_true", help="只获取数据不写飞书")
    parser.add_argument("--test", action="store_true", help="测试模式（文档首行加时间戳）")
    args = parser.parse_args()

    # 1. 数据获取
    print(f"=== 获取实验 {args.flight_id} {args.version} 数据 ===")
    gen = ReportGenerator(
        flight_id=args.flight_id,
        target_version=args.version,
        screenshots_dir=args.screenshots,
    )
    gen.prepare()

    if args.dry_run:
        gen.print_summary()
        return

    # 2. 写入飞书文档
    print(f"\n=== 写入飞书文档 ===")
    doc = FeishuDoc(args.doc_id)
    doc.auth()

    if not args.doc_id:
        exp_name = gen.experiment_data.get("experiment_name") or f"Flight {args.flight_id}"
        title = f"[Launch Notice] {exp_name} - {args.version}"
        doc.create_document(title)

    gen.render(doc, test_mode=args.test)

    # 3. 设置权限
    try:
        doc.set_public_permission("tenant_readable")
    except Exception as e:
        print(f"⚠ 设置权限失败: {e}")
        print("  请手动分享文档")


if __name__ == "__main__":
    main()
