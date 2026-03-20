"""Libra API 客户端

API 权限说明：
- /datatester/experiment/api/ 路径（实验详情）→ 401，不可用
- /datatester/report/api/ 路径（baseuser, lean-data, meta 等）→ 正常
- 实验基本信息通过 conclusion-report-meta 获取（含 experiment_name, start_time 等）
"""
import json
from pathlib import Path

import requests

BASE_URL = "https://libra-sg.tiktok-row.net"
APP_ID_DEFAULT = 22
APP_ID_LIST = -1


class LibraClient:

    def __init__(self, cookies_path=None):
        self.session = requests.Session()
        cookies_path = cookies_path or Path(__file__).parent.parent / "cookies.json"
        self._load_cookies(cookies_path)

    def _load_cookies(self, path):
        """从 Playwright 格式的 cookies.json 加载 cookies（保留 domain）"""
        with open(path, encoding="utf-8") as f:
            cookies_list = json.load(f)
        for c in cookies_list:
            self.session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )

    def _get(self, path, params=None):
        """发起 GET 请求，检查返回码，返回 data 字段"""
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        code = body.get("code")
        if code not in (0, 200):
            raise RuntimeError(f"API error code={code}, msg={body.get('message', '')}, url={url}")
        return body.get("data", body)

    def get_experiment_detail(self, flight_id) -> dict:
        """获取实验详情（通过 conclusion-report-meta）

        返回: {experiment_name, start_time, end_time, start_date, end_date,
               status, versions, flight_id, ...}
        """
        return self.get_conclusion_report_meta(flight_id)

    def get_baseuser(self, flight_id) -> dict:
        """获取用户基数（含版本列表、数据日期）"""
        return self._get(
            f"/datatester/report/api/v3/app/{APP_ID_DEFAULT}/experiment/{flight_id}/baseuser"
        )

    def get_conclusion_report_meta(self, flight_id) -> dict:
        """获取结论报告元信息（指标组列表 + 实验基本信息）

        返回字段包括: experiment_name, start_time, end_time, start_date, end_date,
        versions, metric_groups, status, effected_regions 等
        """
        return self._get(
            f"/datatester/report/api/v3/app/{APP_ID_LIST}/experiment/{flight_id}/conclusion-report-meta",
            params={"mode": "report", "need_metric_group_dims": 0},
        )

    def get_metric_group_meta(self, flight_id, metric_group_id) -> dict:
        """获取单个指标组的详细元信息（含各指标名称）"""
        return self._get(
            f"/datatester/report/api/v3/app/{APP_ID_DEFAULT}/experiment/{flight_id}/metric-group-meta",
            params={
                "is_bundle_report": 0,
                "metric_group": metric_group_id,
                "tag": "important",
                "force_new": "true",
            },
        )

    def get_lean_data(self, flight_id, metric_group_id, start_date, end_date, base_vid,
                      merge_type="total", data_region=None) -> dict:
        """获取指标组的精简数据

        Args:
            merge_type: "total"(累计) 或 "average"(平均)
            data_region: 地区筛选（"EU"/"ROW"/"US"），None=不传（全局数据）
        注意：data_region 默认不传（传不当值可能导致数据异常）
        """
        params = {
            "base_vid": base_vid,
            "metric_group": metric_group_id,
            "start_date": start_date,
            "end_date": end_date,
            "merge_type": merge_type,
            "view_type": "merge",
            "period_type": "d",
            "confidence_threshold": 0.05,
            "data_caliber": 3,
            "isSupportTotal": "true",
            "need_fallback": "true",
            "force_show": 0,
            "capping_corr": 0,
            "capping_decision": 0,
            "combine": 0,
            "mult_cmp_corr": 0,
            "force_query": 0,
            "metric_group_type": "libra",
        }
        if data_region is not None:
            params["data_region"] = data_region
        return self._get(
            f"/datatester/report/api/v3/app/{APP_ID_DEFAULT}/experiment/{flight_id}/lean-data-v2",
            params=params,
        )
