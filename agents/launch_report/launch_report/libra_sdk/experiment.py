"""实验数据处理工具"""
from datetime import datetime, timedelta


class ExperimentHelper:
    """全部静态方法，无状态"""

    @staticmethod
    def identify_base_version(baseuser_list):
        """识别对照组和实验组

        规则：优先 vname=='v0'，fallback 取第一条（Libra 保证第一条是对照组）

        Returns:
            {
                "base_vid": int,
                "base_vname": str,
                "base_users": int,
                "exp_versions": [(vid, vname, users), ...],
            }
        """
        if not baseuser_list:
            raise ValueError("baseuser 列表为空")

        # 优先找 v0
        base = None
        for v in baseuser_list:
            if v["vname"] == "v0":
                base = v
                break
        # fallback: 第一条
        if base is None:
            base = baseuser_list[0]

        exp_versions = [
            (v["vid"], v["vname"], v["baseuser"])
            for v in baseuser_list
            if v["vid"] != base["vid"]
        ]

        return {
            "base_vid": base["vid"],
            "base_vname": base["vname"],
            "base_users": base["baseuser"],
            "exp_versions": exp_versions,
        }

    @staticmethod
    def compute_date_range(baseuser_data, meta):
        """计算数据日期范围

        优先用 meta 中的 start_date/end_date（API 推荐范围），
        fallback 用 start_time 推算。

        Returns:
            (start_date: str, end_date: str, valid: bool)
        """
        start_date = meta.get("start_date") or meta.get("default_selected_start_date")
        end_date = meta.get("end_date") or meta.get("default_selected_end_date")

        # fallback: 从 start_time 推算
        if not start_date:
            start_time = meta.get("start_time")
            if start_time:
                start_date = datetime.fromtimestamp(float(start_time)).strftime("%Y-%m-%d")
            else:
                start_date = (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")

        if not end_date:
            data_date = baseuser_data.get("data_date")
            if data_date:
                end_date = data_date
            else:
                end_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

        # 校验：start <= end
        valid = start_date <= end_date
        return start_date, end_date, valid

    @staticmethod
    def parse_metrics(merge_data, base_vid_str, target_vid_str, name_map=None, fallback_names=None):
        """解析指标组数据，提取目标版本相对于对照组的指标变化

        Args:
            merge_data: lean-data 中的 merge_data 字典
            base_vid_str: 对照组 vid（字符串）
            target_vid_str: 目标实验组 vid（字符串）
            name_map: {metric_id_str: metric_name} 映射（可选，来自 metric-group-meta）
            fallback_names: {metric_id_str: metric_name} 备选映射（来自 lean-data 的 metric_name 字段）

        Returns:
            [{metric_id, name, value, base_value, rel_diff, abs_diff,
              p_val, confidence, significant}, ...]
        """
        name_map = name_map or {}
        fallback_names = fallback_names or {}
        results = []

        for metric_id_str, versions in merge_data.items():
            target_data = versions.get(target_vid_str, {})
            base_data = versions.get(base_vid_str, {})

            if not target_data:
                continue

            # relative_diff 和 confidence 是 {base_vid_str: value} 格式
            rd_dict = target_data.get("relative_diff") or {}
            conf_dict = target_data.get("confidence") or {}
            pv_dict = target_data.get("p_val") or {}
            ad_dict = target_data.get("absolute_diff") or {}

            rel_diff = rd_dict.get(base_vid_str)
            confidence = conf_dict.get(base_vid_str, 0)
            p_val = pv_dict.get(base_vid_str)
            abs_diff = ad_dict.get(base_vid_str)

            # significant: confidence != 0
            significant = confidence != 0

            results.append({
                "metric_id": metric_id_str,
                "name": name_map.get(metric_id_str) or fallback_names.get(metric_id_str, f"metric_{metric_id_str}"),
                "value": target_data.get("value"),
                "base_value": base_data.get("value"),
                "rel_diff": rel_diff,
                "abs_diff": abs_diff,
                "p_val": p_val,
                "confidence": confidence,
                "significant": significant,
            })

        return results

    @staticmethod
    def format_diff(val, significant=False):
        """格式化指标变化值

        精度规则：|pct| >= 1 → 2位, >= 0.1 → 3位, else → 4位
        显著时加标记

        Returns:
            "+1.75%" / "-0.005%" / "Flat" / "N/A"
        """
        if val is None:
            return "N/A"

        pct = val * 100
        abs_pct = abs(pct)

        if abs_pct < 1e-6:
            return "Flat"

        if abs_pct >= 1:
            text = f"{pct:+.2f}%"
        elif abs_pct >= 0.1:
            text = f"{pct:+.3f}%"
        else:
            text = f"{pct:+.4f}%"

        if significant:
            # confidence=1 正向（绿），confidence=-1 负向（红）
            return text
        return text
