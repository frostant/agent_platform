"""实验数据处理工具（digest 用到的子集）"""


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

        base = None
        for v in baseuser_list:
            if v["vname"] == "v0":
                base = v
                break
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
