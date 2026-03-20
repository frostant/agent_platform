# Launch Report — Agent 接手指南

## 项目定位

自包含的 Libra 实验报告生成工具包。从 TikTok Libra 实验平台截图 + 爬取数据 → 生成飞书文档。

**整个文件夹可以直接拷贝到任何位置独立运行**，不依赖外部代码。

## 端到端流程（3 步）

```
Step 1: 截图（Playwright 浏览器自动化）
  capture_screenshots_parallel() → output/{flight_id}/*.png

Step 2: 爬取数据（Libra API）
  crawl() → output/{flight_id}/metrics_data.json

Step 3: 生成报告（飞书文档 API）
  generate_report → 读取截图 + JSON → 写入飞书文档
```

**关键约束：三步必须使用相同的 flight_id、日期范围、实验组 vid，否则数据不一致。**

## 文件结构

```
launch_report/
├── __init__.py              # PKG_DIR, DEFAULT_OUTPUT_DIR 常量
├── .env                     # 飞书凭证（LARK_APP_ID/SECRET/BASE）
├── cookies.json             # Libra 登录态（过期需运行 get_cookies.py 刷新）
├── requirements.txt         # requests, playwright, Pillow, python-dotenv
│
├── generate_report.py       # CLI 入口：生成飞书报告
├── crawl_metrics.py         # CLI 入口：爬取指标数据
├── get_cookies.py           # CLI 入口：打开浏览器获取 cookies
│
├── config/
│   ├── __init__.py          # load_metrics3_config(), load_settings()
│   ├── metrics3.json        # 指标组配置（截图/爬虫/报告共用，改动需三方兼容）
│   └── settings.json        # 运行参数
│
├── libra_sdk/
│   ├── client.py            # LibraClient — Libra API HTTP 客户端
│   ├── experiment.py        # ExperimentHelper — 版本识别、日期计算、指标解析
│   ├── screenshot_v2.py     # 底层截图函数（弹窗处理、DOM 定位、各类型截图）
│   └── screenshot_parallel.py  # 并行截图入口（多浏览器并发）
│
├── feishu_sdk/
│   └── doc.py               # FeishuDoc — 飞书文档 Open API 客户端
│
├── report/
│   └── generator.py         # ReportGenerator — 数据获取 + 飞书文档渲染
│
└── output/                  # 所有产出（截图 + metrics_data.json）
    └── {flight_id}/
```

## 快速开始

### 前置条件

```bash
pip install requests playwright Pillow python-dotenv
playwright install chromium
```

### 获取实验信息

```python
from launch_report.libra_sdk.client import LibraClient
from launch_report.libra_sdk.experiment import ExperimentHelper

client = LibraClient()  # 自动从包内 cookies.json 加载

# 获取版本列表
meta = client.get_conclusion_report_meta(71879109)
baseuser = client.get_baseuser(71879109)
info = ExperimentHelper.identify_base_version(baseuser['baseuser'])

print(f"对照组: {info['base_vname']} (vid={info['base_vid']})")
for vid, vname, users in info['exp_versions']:
    print(f"实验组: {vname} (vid={vid}), users={users:,}")

# 获取日期范围
start, end, valid = ExperimentHelper.compute_date_range(baseuser, meta)
print(f"日期: {start} ~ {end}")
```

### Step 1: 截图

```python
import asyncio
from launch_report import DEFAULT_OUTPUT_DIR
from launch_report.config import load_metrics3_config, get_launch_report_groups
from launch_report.libra_sdk.screenshot_parallel import capture_screenshots_parallel

config = load_metrics3_config()
groups = get_launch_report_groups(config)
# 补充 age_dimension 等额外字段
for mg in config['metric_groups']:
    for g in groups:
        if g['group_id'] == mg['group_id']:
            g['age_dimension'] = mg.get('age_dimension', 'predicted_age_group')

results = asyncio.run(capture_screenshots_parallel(
    flight_id=71879109,
    groups=groups,
    output_dir=str(DEFAULT_OUTPUT_DIR / '71879109'),
    datacenter='ROW',           # ROW=其他机房, EU=EU-TTP
    max_workers=4,
    start_date='2026-03-16',
    end_date='2026-03-19',
    base_vid=75706322,          # 必传：对照组 vid
    target_vid=75706323,        # 必传：目标实验组 vid
))
```

### Step 2: 爬取数据

```python
from launch_report.crawl_metrics import crawl

crawl(
    flight_id=71879109,
    target_version='粗排回调1',   # 版本名（不是 vid）
    output_dir='launch_report/output/71879109',
    start_date='2026-03-16',
    end_date='2026-03-19',
)
# 产出: output/71879109/metrics_data.json
```

### Step 3: 生成飞书报告

```bash
python -m launch_report.generate_report \
    --flight_id 71879109 \
    --version 粗排回调1 \
    --screenshots launch_report/output/71879109 \
    --doc_id LHQxdiSJAo7zJXxjw2pl28yqgsf \
    --test
```

或只看数据摘要（不写飞书）：
```bash
python -m launch_report.generate_report \
    --flight_id 71879109 --version 粗排回调1 \
    --screenshots launch_report/output/71879109 --dry-run
```

---

## 关键参数说明

### flight_id / base_vid / target_vid

- `flight_id`: Libra 实验 ID（URL 中的数字）
- `base_vid`: 对照组 vid（通过 `get_baseuser` API 获取）
- `target_vid`: 目标实验组 vid
- **多实验组时必须指定 vid**，不指定会报 ValueError

### 日期范围

- `start_date` / `end_date`: 格式 `YYYY-MM-DD`
- 截图、爬虫、报告三者**必须使用相同日期**
- 不指定时 crawl_metrics 会自动从 API 获取推荐范围，但截图不会——务必显式传入

### datacenter

- `"ROW"`: 只看其他机房数据（排除 EU）
- `"EU"`: 只看 EU-TTP 机房数据
- `None`: 不筛选（全部）

### versions URL 格式（截图专用）

Libra 页面 URL 必须同时传 base_vid 和 target_vid：
```
?versions=75706322&versions=75706323
```
- 只传 target_vid → 页面把它当基准组，只显示绝对值，无对比差异
- 传版本名 → 不生效，两个实验组都显示

---

## Libra API 踩坑记录

### Cookie 管理

- cookies 文件位置：包根目录 `cookies.json`
- 格式：Playwright JSON（含 name, value, domain, path）
- 加载时**必须保留原始 domain**：`session.cookies.set(name, value, domain=c['domain'])`
- 过期后运行 `python -m launch_report.get_cookies` 重新获取

### API 路径权限

| 路径 | 状态 |
|------|------|
| `/datatester/experiment/api/` | 401 不可用 |
| `/datatester/report/api/` (baseuser, lean-data, conclusion-report-meta) | 正常 |

### API 参数要点

- `app_id`: 实验列表/结论 API 用 `-1`，report 类 API 用 `22`
- `data_region`: **不传**（传任何值导致 EU 数据异常）
- `relative_diff` / `p_val` / `confidence` 是字典 `{base_vid_str: value}`，不是标量
- `confidence`: `1`=显著正向, `-1`=显著负向, `0`=不显著

---

## 截图引擎调试指南

### 弹窗处理：两套函数，用错会出大问题

| 函数 | 机制 | 副作用 | 适用场景 |
|------|------|--------|---------|
| `_dismiss_popups` | 点击关闭按钮 | **触发页面事件，可能导致数据重新加载** | 不要在截图流程中使用 |
| `_hide_overlays` | CSS display:none + DOM 移除 | 无副作用 | 截图流程中一律用这个 |

**规则：截图场景一律用 `_hide_overlays`，每次 `page.screenshot()` 前都要调用。**

### 截图类型

| type | 说明 | 关键函数 |
|------|------|---------|
| `table` | 全表截图 | `_screenshot_table` |
| `table_range` | 指定列范围截图（水平滚动） | `_screenshot_table_range` |
| `chart` | 图表截图（开关图表 → 选指标 → 选趋势类型） | `_screenshot_chart` |
| `age_breakdown` | 多维分析截图（打开 modal → 选维度 → 全选值 → 确认） | `_screenshot_age_breakdown` |

### 常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 截图中有弹窗/浮窗 | `_hide_overlays` 的 CSS selector 不覆盖新类型弹窗 | 在 `_hide_overlays` 中追加 selector |
| 数据表格是空的/"暂无数据" | 数据未加载完就截图了 | 检查 `_wait_for_data_table` 是否被调用且返回值被检查 |
| chart 图表是空白的 | `_wait_for_chart_rendered` 超时（检测逻辑检查单像素，可能误判） | 增大 timeout 或改进检测逻辑 |
| breakdown 找不到维度 | `age_dimension` 配置值与实验实际维度名不匹配 | 在 Libra 页面手动确认维度名，更新 metrics3.json |
| 截图只有绝对值没有百分比 | URL versions 参数格式错误（缺 base_vid 或格式不对） | 必须 `versions=base_vid&versions=target_vid` |
| 截图显示两个实验组 | 没有通过 URL 筛选版本 | 传入正确的 base_vid + target_vid |

### DOM 定位策略

screenshot_v2 使用**语义定位**（不是坐标硬编码）：
- 卡片定位：`findCardWrapper(sidebarName)` — 按 sidebar 名称找 `table-chart-wrapper`
- 图表定位：`findChartContainer(sidebarName)` — 找 canvas → 向上查找卡片容器
- 弹窗定位：`findVisibleModal()` — 找可见的 `.arco-modal`
- 排除逻辑：modal 内的元素、sidebar 区域（x < 250）都会被排除

---

## 飞书文档样式规范

- **彩色摘要行**：仅百分比着色（绿/红），指标名保持默认色
- **颜色跟 rel_diff 正负走**：正值→绿(4)，负值→红(1)
- **百分比格式**：3 位有效数字，`f"{pct:+.3g}%"`
- **text_color 有效范围**：1-7（1=红, 2=橙, 3=黄, 4=绿, 5=蓝, 6=紫, 7=灰）
- **表格总宽**：1000 填满页面

---

## config/metrics3.json 配置格式

每个指标组的完整字段：
```json
{
  "group_id": 7018243,            // Libra 指标组 ID
  "group_name": "Repost Core",    // 简称（报告中显示）
  "sidebar_name": "[social]...",  // Libra 页面左侧栏实际显示名（截图定位用）
  "section": "target",            // 报告分区: impacts / business / target
  "layout": "primary_grid",       // 报告布局: table_pair / primary_grid / vertical
  "primary_pair": 0,              // primary_grid 专用：主指标对索引
  "age_dimension": "predicted_age_group_classifier_global_report",  // breakdown 维度名
  "metrics": [                    // 指标定义（爬虫 + 报告摘要用）
    {"id": 570891, "name": "RepostVV/User", "display_mode": "average"}
  ],
  "screenshots": [                // 截图序列定义
    {"type": "chart", "metric_name": "...", "trend_type": "daily",
     "range": {"start_col": "User", "end_col": "..."}, "display_mode": "cumulative"}
  ]
}
```

**改动 metrics3.json 需确保截图/爬虫/报告三方兼容。**

---

## 依赖关系图

```
generate_report.py ──→ report/generator.py ──→ feishu_sdk/doc.py
                                            ──→ libra_sdk/client.py
                                            ──→ libra_sdk/experiment.py
                                            ──→ config/__init__.py

crawl_metrics.py ────→ libra_sdk/client.py
                 ────→ libra_sdk/experiment.py
                 ────→ config/__init__.py

screenshot_parallel.py ──→ screenshot_v2.py（底层函数）

外部依赖: requests, playwright, Pillow, python-dotenv
外部文件: .env（飞书凭证）, cookies.json（Libra 登录态）
```

---

## 历史决策记录

| 日期 | 决策 | 原因 |
|------|------|------|
| 2026-03-21 | screenshot_parallel 用 `_hide_overlays` 替换 `_dismiss_popups` | `_dismiss_popups` 点击关闭触发数据刷新 |
| 2026-03-21 | URL versions 用 `base_vid&target_vid` 格式 | 只传 target_vid 页面当基准组展示；传版本名不生效 |
| 2026-03-21 | `capture_screenshots_parallel` 强制要求 base_vid + target_vid | 多实验组不指定会导致截图数据不一致 |
| 2026-03-21 | DM 组 age_dimension 改为 `predicted_age_group_classifier_latest_prev_1d_layer` | 原值在部分实验中不存在 |
| 2026-03-21 | 包内所有 import 改为相对导入，消除 sys.path hack | 确保包可独立运行 |
| 2026-03-01 | 颜色跟 rel_diff 正负走，不看 confidence 方向 | confidence 表示业务方向，不代表数值正负 |
| 2026-03-01 | 布局用 Config 驱动（layout 字段） | 每组布局需求不同，需手动控制 |
| 2026-02-28 | metrics3.json 改动需向后兼容 | 截图/爬虫/报告共用此配置 |
