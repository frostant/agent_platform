"""配置加载模块"""
import json
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent


def load_settings() -> dict:
    """加载 settings.json"""
    with open(_CONFIG_DIR / "settings.json", encoding="utf-8") as f:
        return json.load(f)


def load_metrics3_config() -> dict:
    """加载 metrics3.json（飞书报告专用配置）"""
    with open(_CONFIG_DIR / "metrics3.json", encoding="utf-8") as f:
        return json.load(f)


def get_launch_report_groups(config: dict) -> list:
    """从 metrics config 中提取指标组列表"""
    groups = []
    for mg in config.get("metric_groups", []):
        groups.append({
            "group_id": mg["group_id"],
            "group_name": mg["group_name"],
            "sidebar_name": mg.get("sidebar_name", mg["group_name"]),
            "metrics": mg.get("metrics", []),
            "screenshots": mg.get("screenshots", []),
        })
    return groups
