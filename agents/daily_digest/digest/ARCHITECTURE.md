# Digest 模块架构文档

## 模块定位

从 TikTok Libra 实验平台拉取指标数据，生成文本摘要。提供两个入口：单实验查询、全实验每日摘要。

**自包含模块**：所有依赖内联，无需 `sys.path` hack，可独立于父项目使用。

## 目录结构

```
digest/
├── __init__.py       # 公共接口导出
├── config.py         # 配置加载 + 路径常量（COOKIES_PATH, LIBRA_FLIGHT_URL）
├── client.py         # Libra API 客户端（LibraClient）
├── experiment.py     # 实验版本识别（ExperimentHelper.identify_base_version）
├── core.py           # 共享核心：指标配置过滤、数据拉取、格式化输出
├── single.py         # 入口：单实验查询（CLI: python -m digest.single）
├── batch.py          # 入口：全实验每日摘要（CLI: python -m digest.batch）
├── metrics3.json     # 指标组配置（digest 自带副本）
├── cookies.json      # 运行时文件，用户需自行放置
└── ARCHITECTURE.md
```

## 依赖关系

```
single.py ──→ core.py ──→ client.py      (LibraClient, API 调用)
batch.py  ──→ core.py ──→ experiment.py   (ExperimentHelper, 版本识别)
                       ──→ config.py       (load_metrics3_config, 路径常量)
                                ──→ metrics3.json
```

外部依赖仅 `requests`（HTTP 库）和 Python 标准库。

## 核心设计决策

### 1. `query_experiment()` 是纯函数，不修改外部状态

**背景**：旧版 `process_experiment(client, exp, config, collectors)` 通过 side-effect 修改传入的 `collectors` 字典，调用者必须预构造字典结构，函数无直接返回值。这导致单实验场景无法复用。

**方案**：`query_experiment()` 返回结构化 dict，调用者自行决定如何聚合结果。

```python
def query_experiment(client, flight_id, config, exp_name=None, start_time_ts=None,
                     start_date=None, end_date=None, data_region=None) -> dict:
    """
    返回:
    {
        'name': str,              # 实验名称
        'flight_id': int,         # 实验 ID
        'start_date': str,        # 实际使用的开始日期
        'end_date': str,          # 实际使用的结束日期
        'versions_results': list, # [(vname, [group_result, ...]), ...]
        'warnings': list,         # 警告信息
        'status': str,            # 'ok'|'computing'|'skip'|'fail'
        'skip_reason': str|None,  # status 非 ok 时的原因
        'error': str|None,        # status='fail' 时的异常信息
    }
    """
```

### 2. 日期范围：手动优先，自动兜底

```python
# 优先级：手动指定 > 自动计算
# 自动计算：start = 实验开启时间，end = now - 2 天
# 支持部分覆盖：只传 start_date 或只传 end_date
```

### 3. data_region 参数透传

```python
# 有效值：EU / ROW / US（仅这三个）
# None = 不传给 API（全局数据，默认行为）
# 传值时加入 get_lean_data 的 params["data_region"]
#
# ⚠️ 历史踩坑：data_region 传不当值会导致 EU 数据异常
# 因此默认不传，由调用者显式指定
```

### 4. LibraClient 全局实例 + 显式传 flight_id

**旧版**：`LibraDataFetcher(flight_id, cookies_path)` 每个实验创建一个实例。
**新版**：`LibraClient(cookies_path)` 全局一个，所有函数显式传 `flight_id`。

原因：batch 模式遍历多个实验时，共享一个 session 更高效。

### 5. per-metric display_mode（average/cumulative）

同一指标组内不同指标可能需要不同 merge_type。`fetch_experiment_metrics()` 检查每个组是否有 `display_mode="average"` 的指标，如有则额外调一次 `merge_type="average"` 的 API。

### 6. 自包含：无外部代码依赖

`client.py`、`experiment.py`、`config.py` 从 `libra_sdk/` 和 `config/` 内联而来。`metrics3.json` 随模块分发。`cookies.json` 为运行时文件，用户需自行放置到 `digest/` 目录下。

## 函数清单

### config.py

| 函数/常量 | 用途 |
|-----------|------|
| `COOKIES_PATH` | cookies.json 路径（digest/ 目录下） |
| `LIBRA_FLIGHT_URL` | Libra 实验报告 URL 模板 |
| `load_metrics3_config()` | 加载 metrics3.json |

### client.py

| 方法 | 用途 |
|------|------|
| `LibraClient(cookies_path)` | 构造器，加载 cookies |
| `.get_baseuser(flight_id)` | 获取版本列表 |
| `.get_conclusion_report_meta(flight_id)` | 获取实验元信息 |
| `.get_lean_data(flight_id, gid, start, end, base_vid, merge_type, data_region)` | 获取指标数据 |

### experiment.py

| 方法 | 用途 |
|------|------|
| `ExperimentHelper.identify_base_version(baseuser_list)` | 识别对照组/实验组 |

### core.py

| 函数 | 签名 | 用途 |
|------|------|------|
| `load_digest_config()` | `() -> dict` | 从 metrics3.json 加载 digest=true 的指标组 |
| `fetch_running_experiments(session)` | `(session) -> list` | GET 所有运行中实验 |
| `get_version_info(client, flight_id)` | `(...) -> (base_vid, base_vname, exp_versions)` | 获取对照组和实验组 |
| `get_date_range(start_time_ts)` | `(ts) -> (start, end, valid)` | 自动计算日期范围 |
| `filter_recent_experiments(experiments)` | `(list) -> (recent, skipped_count)` | 筛选 14 天内实验 |
| `fetch_experiment_metrics(...)` | `(..., data_region=None) -> (versions_results, warnings, status)` | 拉取一个实验全部指标 |
| `query_experiment(...)` | 见上方签名 | **核心接口**：查询单实验 |
| `format_pct(rel_diff, confidence)` | `(float, int) -> str` | 百分比 + 显著性 emoji |
| `build_summary_table(all_results, config)` | `(list, dict) -> str` | Markdown 汇总表 |
| `build_detail(...)` | `(...) -> str` | 单实验详情文本 |

### single.py

| 函数 | 用途 |
|------|------|
| `query_single_experiment(flight_id, start_date, end_date, data_region)` | 单实验查询 |

CLI: `python -m digest.single <flight_id> [--start_date] [--end_date] [--region {EU,ROW,US}]`

### batch.py

| 函数 | 用途 |
|------|------|
| `run_daily_digest()` | 全实验摘要（含重试） |

CLI: `python -m digest.batch`

## 数据流

### 单实验

```
CLI args (flight_id, --start_date, --end_date, --region)
  → LibraClient(cookies.json)
  → client.get_conclusion_report_meta(flight_id)
  → query_experiment(client, flight_id, config, ...)
    → get_version_info → client.get_baseuser → ExperimentHelper.identify_base_version
    → get_date_range() 或使用手动日期
    → fetch_experiment_metrics → client.get_lean_data(data_region=...)
    → return {status, versions_results, ...}
  → build_detail() → print
```

### 全实验

```
  → fetch_running_experiments(session)
  → filter_recent_experiments()
  → for exp: query_experiment(...)
  → 重试 computing（RETRY_WAITS = [15, 30, 60]s）
  → build_summary_table() + build_detail()
  → stdout + output/digest_{date}_{short,full}.txt
```

## 指标数据结构

### metrics3.json 中的 digest 配置

```json
{
  "metric_groups": [
    {
      "group_id": 123, "group_name": "核心指标", "digest": true,
      "metrics": [
        {"id": 456, "name": "DAU", "short": "DAU", "digest_rule": "always", "display_mode": null},
        {"id": 789, "name": "人均时长", "short": "时长", "digest_rule": "optional", "display_mode": "average"}
      ]
    }
  ]
}
```

- `digest: true` — 该组参与 digest
- `digest_rule`: `"always"` 总是显示 / `"optional"` 仅显著时显示
- `display_mode`: `null`=cumulative / `"average"`=average merge_type
- `short` — 汇总表列头简称

### query_experiment 返回的 versions_results

```python
versions_results = [
    ("v1", [  # 版本名, 指标组列表
        {
            "group_name": "核心指标",
            "metrics": [
                {
                    "metric_id": 456, "name": "DAU", "short": "DAU", "rule": "always",
                    "rel_diff": 0.0123,    # 相对差异（小数，非百分比）
                    "confidence": 1,       # 1=显著正向, -1=显著负向, 0=不显著
                    "error": None,         # 有错误时为描述字符串
                },
            ],
        },
    ]),
]
```

## 运行前置条件

1. `cookies.json` 放在 `digest/` 目录下（Playwright 格式，含 `.tiktok-row.net` 域的认证 cookies）
2. `pip install requests`（唯一外部依赖）
