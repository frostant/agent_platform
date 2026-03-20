"""Launch Report — 自包含的 Libra 实验报告生成包

端到端流程：截图 + 爬虫数据 → 飞书文档

主要入口：
    - generate_report.py: 生成飞书报告
    - crawl_metrics.py: 爬取指标数据
    - get_cookies.py: 获取/刷新 Libra cookies
    - libra_sdk.screenshot_parallel: 并行截图
"""
from pathlib import Path

# 包根目录（所有相对路径的基准）
PKG_DIR = Path(__file__).parent

# 默认输出目录（截图、爬虫数据等产出都放在这里）
DEFAULT_OUTPUT_DIR = PKG_DIR / "output"
