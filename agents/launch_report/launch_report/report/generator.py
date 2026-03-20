"""Launch Report 生成器 — 截图 + 数据 → 飞书文档

用法:
    from report.generator import ReportGenerator
    from feishu_sdk import FeishuDoc

    gen = ReportGenerator(flight_id=71732795, target_version="v9",
                          screenshots_dir="output/LYT0.01")
    gen.prepare()
    doc = FeishuDoc()
    doc.auth()
    doc.create_document("[Launch Notice] 实验名 - v9")
    gen.render(doc)
"""

import json
import time
from datetime import datetime
from pathlib import Path

from ..feishu_sdk import FeishuDoc
from ..libra_sdk.client import LibraClient
from ..libra_sdk.experiment import ExperimentHelper
from ..config import load_metrics3_config

LIBRA_REPORT_URL = "https://libra-sg.tiktok-row.net/libra/flight/{flight_id}/report/main"

# section 显示名映射
SECTION_TITLES = {
    "impacts": "Impacts",
    "business": "Business Must-check Metrics  // 业务必看指标",
    "target": "Target Metrics & Intermediate Metrics  // 目标指标&中间指标",
}

# impacts section 内的组排序
IMPACTS_ORDER = [
    "Core-Active Days",
    "Active Hours",
    "Core-Key Metrics",
]

# target section 内的组排序
TARGET_ORDER = [
    "Social Interaction",
    "Repost Core Metrics",
    "Repost Interaction",
    "DM",
    "VT Multi Queue",
]


class ReportGenerator:
    """Launch Report 生成器

    两阶段工作流：
    1. prepare() — 从 Libra API 获取数据，解析指标，生成摘要
    2. render(doc) — 写入飞书文档
    """

    def __init__(self, flight_id, target_version, screenshots_dir=None, cookies_path=None):
        self.flight_id = flight_id
        self.target_version = target_version
        self.screenshots_dir = Path(screenshots_dir) if screenshots_dir else None
        self.cookies_path = cookies_path
        self.config = load_metrics3_config()

        # prepare() 后填充
        self.experiment_data = None
        self.groups_data = []  # 每个 group 的配置 + 解析后的指标

    def prepare(self):
        """阶段1：获取实验数据

        优先从 screenshots_dir/metrics_data.json 加载缓存，
        无缓存时 fallback 到实时 API 调用。
        """
        # 自动检测缓存
        if self.screenshots_dir:
            cache_path = self.screenshots_dir / "metrics_data.json"
            if cache_path.exists():
                print(f"发现缓存数据: {cache_path}")
                self.load_from_cache(cache_path)
                return

        print("无缓存，从 API 实时获取...")
        self._fetch_from_api()

    def load_from_cache(self, cache_path):
        """从 metrics_data.json 加载缓存数据，跳过 API 调用"""
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)

        self.experiment_data = {
            "flight_id": cached["flight_id"],
            "experiment_name": cached["experiment_name"],
            "base_vid": cached["base_vid"],
            "base_vname": cached["base_vname"],
            "base_users": cached["base_users"],
            "target_vid": cached["target_vid"],
            "target_vname": cached["target_vname"],
            "target_users": cached["target_users"],
            "start_date": cached["start_date"],
            "end_date": cached["end_date"],
        }

        # 重建 groups_data（合并 config 的 screenshots 等字段和缓存的 metrics 数据）
        config_groups = self.config["metric_groups"]
        config_by_id = {str(g["group_id"]): g for g in config_groups}

        self.groups_data = []
        for gid_str, group_cache in cached["groups"].items():
            cfg = config_by_id.get(gid_str, {})
            self.groups_data.append({
                **cfg,
                "group_id": int(gid_str) if gid_str.isdigit() else gid_str,
                "group_name": group_cache["group_name"],
                "parsed_metrics": group_cache.get("metrics", []),
            })

        total = len(self.groups_data)
        sig_groups = sum(1 for g in self.groups_data
                         if any(m.get("significant") for m in g.get("parsed_metrics", [])))
        print(f"从缓存加载: {total} 个指标组, {sig_groups} 个有显著指标")
        print(f"数据日期: {cached['start_date']} ~ {cached['end_date']}")
        print(f"爬取时间: {cached.get('crawl_time', 'N/A')}")

    def _fetch_from_api(self):
        """从 Libra API 实时获取数据（原 prepare 逻辑）"""
        client = LibraClient(self.cookies_path)

        # 1. 实验基本信息
        print("获取实验基本信息...")
        meta = client.get_conclusion_report_meta(self.flight_id)
        exp_name = meta.get("experiment_name", "")

        # 2. 版本信息
        print("获取版本信息...")
        baseuser_data = client.get_baseuser(self.flight_id)
        version_info = ExperimentHelper.identify_base_version(
            baseuser_data.get("baseuser", [])
        )

        # 找目标实验组
        target_vid = None
        target_users = 0
        for vid, vname, users in version_info["exp_versions"]:
            if vname == self.target_version:
                target_vid = vid
                target_users = users
                break
        if target_vid is None:
            available = [vname for _, vname, _ in version_info["exp_versions"]]
            raise ValueError(f"未找到实验组 {self.target_version}，可用: {available}")

        # 3. 日期范围
        start_date, end_date, valid = ExperimentHelper.compute_date_range(
            baseuser_data, meta
        )
        print(f"  对照组 {version_info['base_vname']} (vid={version_info['base_vid']}): {version_info['base_users']:,}")
        print(f"  实验组 {self.target_version} (vid={target_vid}): {target_users:,}")
        print(f"  日期范围: {start_date} ~ {end_date}")

        self.experiment_data = {
            "flight_id": self.flight_id,
            "experiment_name": exp_name,
            "base_vid": version_info["base_vid"],
            "base_vname": version_info["base_vname"],
            "base_users": version_info["base_users"],
            "target_vid": target_vid,
            "target_vname": self.target_version,
            "target_users": target_users,
            "start_date": start_date,
            "end_date": end_date,
        }

        # 4. 遍历指标组获取数据
        config_groups = self.config["metric_groups"]
        print(f"\n获取 {len(config_groups)} 个指标组数据...")
        self.groups_data = []

        for i, group_cfg in enumerate(config_groups):
            gid = group_cfg["group_id"]
            gname = group_cfg["group_name"]
            try:
                lean_data = client.get_lean_data(
                    self.flight_id, gid, start_date, end_date,
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
                        self.flight_id, gid, start_date, end_date,
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

                self.groups_data.append({
                    **group_cfg,
                    "parsed_metrics": metrics,
                })
            except Exception as e:
                print(f"  [{i+1}/{len(config_groups)}] {gname}: 获取失败 - {e}")
                self.groups_data.append({
                    **group_cfg,
                    "parsed_metrics": [],
                    "error": str(e),
                })

    def render(self, doc: FeishuDoc, test_mode=False):
        """阶段2：写入飞书文档

        Args:
            doc: 飞书文档客户端
            test_mode: 测试模式，在第一行插入时间戳
        """
        data = self.experiment_data

        # 清空文档
        print("\n清空文档...")
        doc.delete_all_blocks()
        time.sleep(1)

        # 测试模式：时间戳放第一行
        if test_mode:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            doc.append_text(f"[TEST] 生成时间: {ts}",
                            text_color=FeishuDoc.color_name_to_int("gray"))

        # H1 标题
        exp_name = data["experiment_name"] or f"Flight {data['flight_id']}"
        doc.append_heading(
            f"[Launch Notice][ROW] {exp_name} - {data['target_vname']}", level=1
        )

        # Libra 链接
        doc.append_text(LIBRA_REPORT_URL.format(flight_id=data["flight_id"]))

        # 按 section 分组
        sections = self._classify_by_section()

        # H2: Impacts
        if sections.get("impacts"):
            doc.append_heading(SECTION_TITLES["impacts"], level=2)
            for group in sections["impacts"]:
                self._write_group(doc, group)

        # H2: Business
        if sections.get("business"):
            doc.append_heading(SECTION_TITLES["business"], level=2)
            for group in sections["business"]:
                self._write_group(doc, group)

        # H2: Target
        if sections.get("target"):
            doc.append_heading(SECTION_TITLES["target"], level=2)
            for group in sections["target"]:
                self._write_group(doc, group)

        # 底部信息
        doc.append_divider()
        doc.append_text(LIBRA_REPORT_URL.format(flight_id=data["flight_id"]))
        doc.append_text(
            f"数据日期: {data['start_date']} ~ {data['end_date']}  |  "
            f"{data['base_vname']}: {data['base_users']:,}  |  "
            f"{data['target_vname']}: {data['target_users']:,}"
        )

        total = len(self.groups_data)
        print(f"\n报告写入完成！共写入 {total} 个指标组")
        print(f"文档链接: https://bytedance.sg.larkoffice.com/docx/{doc.document_id}")

    def _classify_by_section(self):
        """按 section 字段分组，impacts 组按预定义顺序排序"""
        sections = {"impacts": [], "business": [], "target": []}
        for group in self.groups_data:
            section = group.get("section", "target")
            sections.setdefault(section, []).append(group)

        # impacts 组按 IMPACTS_ORDER 排序
        sections["impacts"].sort(
            key=lambda g: IMPACTS_ORDER.index(g["group_name"])
            if g["group_name"] in IMPACTS_ORDER else 999
        )
        # target 组按 TARGET_ORDER 排序
        sections["target"].sort(
            key=lambda g: TARGET_ORDER.index(g["group_name"])
            if g["group_name"] in TARGET_ORDER else 999
        )
        return sections

    def _write_group(self, doc, group):
        """写入单个指标组：H3 标题 + 彩色摘要 + 截图序列"""
        gname = group["group_name"]
        metrics = group.get("parsed_metrics", [])

        # H3: 指标组名
        doc.append_heading(gname, level=3)

        # 彩色摘要（text block，每个指标独立着色）
        self._write_colored_summary(doc, metrics)

        # 截图
        self._insert_screenshots(doc, group)

        print(f"  ✓ {gname}")

    @staticmethod
    def _format_pct(val):
        """格式化百分比，保留 3 位有效数字"""
        if val is None:
            return "N/A"
        pct = val * 100
        if abs(pct) < 1e-10:
            return "+0.000%"
        return f"{pct:+.3g}%"

    def _write_colored_summary(self, doc, metrics):
        """写入带颜色的摘要行

        显著指标按 rel_diff 正负用背景色标记：
        - rel_diff > 0 → 百分比绿色背景
        - rel_diff < 0 → 百分比红色背景
        不显著 → 「指标名 Flat」默认色
        """
        if not metrics:
            doc.append_text("Row: No data")
            return

        BG_GREEN = FeishuDoc.color_name_to_int("green")  # 4
        BG_RED = FeishuDoc.color_name_to_int("red")      # 1

        # 构建多色 text_run 元素列表
        elements = [{"text_run": {"content": "Row： ", "text_element_style": {"bold": True}}}]

        # 显著指标优先（按变化绝对值降序），然后非显著
        sig = [m for m in metrics if m["significant"]]
        non_sig = [m for m in metrics if not m["significant"]]
        sig.sort(key=lambda m: abs(m.get("rel_diff") or 0), reverse=True)
        ordered = sig + non_sig

        for i, m in enumerate(ordered):
            if i > 0:
                elements.append({"text_run": {"content": ", ", "text_element_style": {}}})

            name = m.get("name", "unknown")
            rel_diff = m.get("rel_diff")
            significant = m.get("significant", False)

            if significant and rel_diff is not None:
                # 显著：百分比用背景色标记
                diff_str = self._format_pct(rel_diff)
                bg = BG_GREEN if rel_diff >= 0 else BG_RED
                elements.append({"text_run": {
                    "content": f"{name} ",
                    "text_element_style": {},
                }})
                elements.append({"text_run": {
                    "content": diff_str,
                    "text_element_style": {"background_color": bg},
                }})
            else:
                # 不显著 → Flat
                elements.append({"text_run": {
                    "content": f"{name} Flat",
                    "text_element_style": {},
                }})

        # 构建 text block 并追加
        block = {"block_type": 2, "text": {"elements": elements}}
        doc.append_blocks(doc.document_id, [block])

    # ── 截图布局 ────────────────────────────────────────

    def _insert_screenshots(self, doc, group):
        """根据 config 中的 layout 字段选择截图布局方式"""
        if not self.screenshots_dir:
            return

        layout = group.get("layout", "vertical")

        if layout == "table_pair":
            self._layout_table_pair(doc, group)
        elif layout == "primary_grid":
            self._layout_primary_grid(doc, group)
        else:
            self._layout_vertical(doc, group)

    def _layout_table_pair(self, doc, group):
        """table_pair: 每对截图用 1×2 表格并排"""
        gid = group["group_id"]
        screenshots = group.get("screenshots", [])
        pairs = self._detect_pairs(screenshots)

        i = 0
        for left_idx, right_idx in pairs:
            # 跳过 pair 之前的单独截图
            while i < left_idx:
                self._insert_single(doc, gid, i, screenshots[i])
                i += 1
            # 并排
            left_path = self._find_screenshot(gid, left_idx, screenshots[left_idx])
            right_path = self._find_screenshot(gid, right_idx, screenshots[right_idx])
            self._insert_pair_as_table(doc, left_path, right_path)
            i = right_idx + 1

        # 剩余单独截图
        while i < len(screenshots):
            self._insert_single(doc, gid, i, screenshots[i])
            i += 1

    def _layout_primary_grid(self, doc, group):
        """primary_grid: 主指标对全宽 + 次要指标对用 Grid 按类型并排

        布局结构（包裹在 1×1 表格容器内）：
        ┌──────────────────────────────────────┐
        │  primary chart            [全宽]      │
        │  primary breakdown        [全宽]      │
        │                                       │
        │  ┌─ Grid ──────┬──────────────────┐  │
        │  │ chart A      │ chart B          │  │
        │  └─────────────┴──────────────────┘  │
        │  ┌─ Grid ──────┬──────────────────┐  │
        │  │ breakdown A  │ breakdown B      │  │
        │  └─────────────┴──────────────────┘  │
        └──────────────────────────────────────┘
        """
        gid = group["group_id"]
        screenshots = group.get("screenshots", [])
        primary_pair_idx = group.get("primary_pair", 0)

        pairs = self._detect_pairs(screenshots)
        if not pairs:
            self._layout_vertical(doc, group)
            return

        # 分离主指标对和次要对
        primary = pairs[primary_pair_idx] if primary_pair_idx < len(pairs) else pairs[0]
        secondary = [p for i, p in enumerate(pairs) if i != primary_pair_idx]

        # 创建 1×1 表格容器
        try:
            table_block_id, cells = doc.create_empty_table(1, 1)
            cell_id = cells[0]
            # 记录默认 block 数量（稍后删除）
            _cell_detail = doc.get_block(cell_id)
            _default_count = len(
                _cell_detail.get("data", {}).get("block", {}).get("children", [])
            )
        except Exception as e:
            print(f"    ⚠ 创建容器表格失败，回退垂直: {e}")
            self._layout_vertical(doc, group)
            return

        # 主指标对：全宽（chart + breakdown 各占一行）
        primary_chart = self._find_screenshot(gid, primary[0], screenshots[primary[0]])
        primary_bd = self._find_screenshot(gid, primary[1], screenshots[primary[1]])

        if primary_chart:
            self._insert_image_in_block(doc, cell_id, str(primary_chart))
        if primary_bd:
            self._insert_image_in_block(doc, cell_id, str(primary_bd))

        # 次要对：按类型用 Grid 并排
        if secondary:
            # 收集所有次要 charts 和 breakdowns
            sec_charts = []
            sec_bds = []
            for left_idx, right_idx in secondary:
                chart_path = self._find_screenshot(gid, left_idx, screenshots[left_idx])
                bd_path = self._find_screenshot(gid, right_idx, screenshots[right_idx])
                if chart_path:
                    sec_charts.append(str(chart_path))
                if bd_path:
                    sec_bds.append(str(bd_path))

            # Grid: charts 并排
            if len(sec_charts) >= 2:
                self._insert_grid_in_block(doc, cell_id, sec_charts)
            elif sec_charts:
                self._insert_image_in_block(doc, cell_id, sec_charts[0])

            # Grid: breakdowns 并排
            if len(sec_bds) >= 2:
                self._insert_grid_in_block(doc, cell_id, sec_bds)
            elif sec_bds:
                self._insert_image_in_block(doc, cell_id, sec_bds[0])

        # 删除 cell 开头的默认空段落（内容都追加在后面，默认段落在 index 0~_default_count-1）
        if _default_count > 0:
            try:
                del_url = (
                    f"{doc.api_base}/docx/v1/documents/{doc.document_id}"
                    f"/blocks/{cell_id}/children/batch_delete"
                    f"?document_revision_id=-1"
                )
                import requests as _req
                _req.delete(
                    del_url,
                    json={"start_index": 0, "end_index": _default_count},
                    headers=doc._headers(),
                    timeout=10,
                )
            except Exception:
                pass

    def _layout_vertical(self, doc, group):
        """vertical: 所有截图垂直堆叠"""
        gid = group["group_id"]
        screenshots = group.get("screenshots", [])
        for i, spec in enumerate(screenshots):
            self._insert_single(doc, gid, i, spec)

    # ── 截图工具方法 ────────────────────────────────────

    def _detect_pairs(self, screenshots):
        """检测连续的 (非breakdown + breakdown) 对

        Returns:
            list of (left_idx, right_idx)
        """
        pairs = []
        i = 0
        while i < len(screenshots) - 1:
            left = screenshots[i]
            right = screenshots[i + 1]
            if left["type"] != "age_breakdown" and right["type"] == "age_breakdown":
                pairs.append((i, i + 1))
                i += 2
            else:
                i += 1
        return pairs

    def _find_screenshot(self, gid, idx, spec):
        """按 glob 查找截图文件，返回路径或 None"""
        stype = spec["type"]
        pattern = f"*_mg_{gid}_{idx:02d}_{stype}.png"
        matches = list(self.screenshots_dir.glob(pattern))
        return matches[0] if matches else None

    def _insert_single(self, doc, gid, idx, spec):
        """插入单张截图到文档末尾"""
        fpath = self._find_screenshot(gid, idx, spec)
        if fpath:
            try:
                doc.append_image(str(fpath))
                time.sleep(0.5)
            except Exception as e:
                print(f"    ⚠ 截图上传失败 {fpath.name}: {e}")
                doc.append_text(
                    f"[截图上传失败: {fpath.name}]",
                    text_color=FeishuDoc.color_name_to_int("gray"),
                )
        else:
            fname = f"mg_{gid}_{idx:02d}_{spec['type']}.png"
            doc.append_text(
                f"[截图缺失: {fname}]",
                text_color=FeishuDoc.color_name_to_int("gray"),
            )

    def _insert_pair_as_table(self, doc, left_path, right_path):
        """用 1×2 表格并排插入两张截图"""
        if not left_path and not right_path:
            return
        if not left_path or not right_path:
            fpath = left_path or right_path
            doc.append_image(str(fpath))
            time.sleep(0.5)
            return
        try:
            table_block_id, cells = doc.create_empty_table(1, 2)
            doc.write_table_cell_image(cells[0], str(left_path))
            time.sleep(0.5)
            doc.write_table_cell_image(cells[1], str(right_path))
            time.sleep(0.5)
        except Exception as e:
            print(f"    ⚠ 表格并排失败，回退到垂直: {e}")
            doc.append_image(str(left_path))
            time.sleep(0.5)
            doc.append_image(str(right_path))
            time.sleep(0.5)

    def _insert_image_in_block(self, doc, parent_block_id, image_path):
        """在指定容器 block 内插入图片"""
        try:
            result = doc.append_blocks(
                parent_block_id, [{"block_type": 27, "image": {}}]
            )
            if result.get("code") != 0:
                print(f"    ⚠ 容器内创建图片失败: {result.get('msg')}")
                return
            img_bid = result["data"]["children"][0]["block_id"]
            file_token, w, h = doc.upload_image(image_path, parent_node=img_bid)
            doc.replace_image(img_bid, file_token)
            time.sleep(0.5)
        except Exception as e:
            print(f"    ⚠ 容器内图片插入失败: {e}")

    def _insert_grid_in_block(self, doc, parent_block_id, image_paths):
        """在指定容器 block 内创建 Grid 并排插入多张图片"""
        col_count = len(image_paths)
        try:
            grid_block = {"block_type": 24, "grid": {"column_size": col_count}}
            result = doc.append_blocks(parent_block_id, [grid_block])
            if result.get("code") != 0:
                print(f"    ⚠ 创建 Grid 失败: {result.get('msg')}")
                return

            grid_data = result["data"]["children"][0]
            column_ids = grid_data.get("children", [])

            for col_idx, img_path in enumerate(image_paths):
                if col_idx >= len(column_ids):
                    break
                col_id = column_ids[col_idx]
                # 用 write_table_cell_image 替代 _insert_image_in_block
                # 它会处理默认空段落的清理
                doc.write_table_cell_image(col_id, img_path)
                time.sleep(0.5)
        except Exception as e:
            print(f"    ⚠ Grid 插入失败: {e}")

    def print_summary(self):
        """打印数据摘要（dry-run 模式）"""
        data = self.experiment_data
        print(f"\n{'='*60}")
        print(f"实验: {data['experiment_name']} (flight_id={data['flight_id']})")
        print(f"对照组: {data['base_vname']} ({data['base_users']:,})")
        print(f"实验组: {data['target_vname']} ({data['target_users']:,})")
        print(f"日期: {data['start_date']} ~ {data['end_date']}")
        print(f"{'='*60}")

        sections = self._classify_by_section()
        for section_name, groups in sections.items():
            if not groups:
                continue
            print(f"\n[{SECTION_TITLES.get(section_name, section_name)}]")
            for group in groups:
                metrics = group.get("parsed_metrics", [])
                sig = [m for m in metrics if m.get("significant")]
                parts = [f"{m['name']} {self._format_pct(m.get('rel_diff'))}" for m in sig]
                summary = ", ".join(parts) if parts else "Flat"
                n_screenshots = len(group.get("screenshots", []))
                print(f"  {group['group_name']}: {summary} ({n_screenshots} 截图)")
