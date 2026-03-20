"""digest 配置加载"""
import json
from pathlib import Path

_DIR = Path(__file__).parent

COOKIES_PATH = _DIR / "cookies.json"
LIBRA_FLIGHT_URL = "https://libra-sg.tiktok-row.net/libra/flight/{flight_id}/report/main"


def load_metrics3_config() -> dict:
    """加载 metrics3.json（digest 自带副本）"""
    with open(_DIR / "metrics3.json", encoding="utf-8") as f:
        return json.load(f)
