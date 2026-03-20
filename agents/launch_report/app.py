"""实验报告生成 — Agent 服务

端到端流程：Libra 截图 → 指标爬取 → 飞书文档生成

API:
  GET  /health              健康检查
  POST /crawl               爬取指标数据
  POST /screenshot          截图指标组
  POST /generate            生成飞书文档（完整流程）
  GET  /outputs             列出已有的输出目录
  GET  /                    前端页面
"""

import logging
import time
import json
import traceback
import sys
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# launch_report 是自包含模块
sys.path.insert(0, str(Path(__file__).parent / "launch_report"))

from libra_sdk.client import LibraClient
from libra_sdk.experiment import ExperimentHelper
from config import load_metrics3_config, load_settings

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent / "data"
LOG_DIR.mkdir(exist_ok=True)

OUTPUT_DIR = Path(__file__).parent / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COOKIES_PATH = Path(__file__).parent / "launch_report" / "cookies.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "launch_report.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("launch_report")

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="实验报告生成")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000)
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({duration}ms)")
    return response


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class CrawlRequest(BaseModel):
    flight_id: int
    version: Optional[str] = None  # 实验版本名，如 "v1"
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class ScreenshotRequest(BaseModel):
    flight_id: int
    version: Optional[str] = None
    group_ids: Optional[List[int]] = None  # 指定截图的指标组 ID，None=全部


class GenerateRequest(BaseModel):
    flight_id: int
    version: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    doc_id: Optional[str] = None  # 已有飞书文档 ID，None=新建


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "cookies_configured": COOKIES_PATH.exists(),
        "output_dir": str(OUTPUT_DIR),
    }


@app.post("/crawl")
def crawl(req: CrawlRequest):
    """爬取指标数据，保存 metrics_data.json"""
    logger.info(f"爬取指标: flight_id={req.flight_id}, version={req.version}")

    if not COOKIES_PATH.exists():
        raise HTTPException(500, "cookies.json 不存在")

    try:
        client = LibraClient(COOKIES_PATH)
        config = load_metrics3_config()

        # 获取实验信息
        meta = client.get_conclusion_report_meta(req.flight_id)
        exp_name = meta.get('experiment_name', f'实验 {req.flight_id}')
        baseuser_data = client.get_baseuser(req.flight_id)
        version_info = ExperimentHelper.identify_base_version(baseuser_data.get('baseuser', []))

        base_vid = version_info['base_vid']
        base_vname = version_info['base_vname']

        # 找目标版本
        target = None
        for vid, vname, users in version_info['exp_versions']:
            if req.version and vname == req.version:
                target = (vid, vname, users)
                break
        if not target and version_info['exp_versions']:
            target = version_info['exp_versions'][0]
        if not target:
            raise HTTPException(400, "无实验组版本")

        target_vid, target_vname, target_users = target

        # 日期范围
        start_date = req.start_date or meta.get('start_date')
        end_date = req.end_date or meta.get('end_date')
        if not start_date or not end_date:
            _, computed = ExperimentHelper.compute_date_range(baseuser_data, meta)
            start_date = start_date or computed[0]
            end_date = end_date or computed[1]

        # 爬取每个指标组
        groups_data = {}
        for group in config.get('metric_groups', []):
            gid = group['group_id']
            try:
                lean_data = client.get_lean_data(req.flight_id, gid, start_date, end_date, base_vid)
                metrics = ExperimentHelper.parse_metrics(
                    lean_data.get('merge_data', {}),
                    str(base_vid), str(target_vid),
                )
                groups_data[str(gid)] = {
                    'group_name': group['group_name'],
                    'section': group.get('section', ''),
                    'metrics': metrics,
                }
            except Exception as e:
                logger.warning(f"指标组 {gid} 爬取失败: {e}")
                groups_data[str(gid)] = {'group_name': group['group_name'], 'error': str(e)}

        # 保存
        suffix = f"_{target_vname}" if req.version else "_final"
        out_dir = OUTPUT_DIR / f"{req.flight_id}{suffix}"
        out_dir.mkdir(parents=True, exist_ok=True)

        result = {
            'flight_id': req.flight_id,
            'experiment_name': exp_name,
            'base_vid': base_vid,
            'base_vname': base_vname,
            'base_users': version_info['base_users'],
            'target_vid': target_vid,
            'target_vname': target_vname,
            'target_users': target_users,
            'start_date': start_date,
            'end_date': end_date,
            'groups': groups_data,
        }

        data_path = out_dir / 'metrics_data.json'
        data_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding='utf-8')

        logger.info(f"指标数据已保存: {data_path}")
        return {"ok": True, "output": str(out_dir), "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"爬取失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"爬取失败: {e}")


@app.get("/outputs")
def list_outputs():
    """列出所有输出目录"""
    outputs = []
    if OUTPUT_DIR.exists():
        for d in sorted(OUTPUT_DIR.iterdir(), reverse=True):
            if d.is_dir():
                files = list(d.glob('*'))
                outputs.append({
                    'name': d.name,
                    'path': str(d),
                    'files': len(files),
                    'has_metrics': (d / 'metrics_data.json').exists(),
                    'screenshots': len(list(d.glob('*.png'))),
                })
    return outputs


# ---------------------------------------------------------------------------
# 前端页面
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>实验报告生成</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7f8fa;color:#1f2329}
.container{max-width:720px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;font-weight:600;margin-bottom:16px}
.card{background:#fff;border-radius:10px;border:1px solid #e5e6eb;padding:16px;margin-bottom:12px}
.card-title{font-size:14px;font-weight:500;color:#646a73;margin-bottom:10px}
.row{display:flex;gap:10px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
.row label{font-size:13px;color:#646a73;min-width:70px}
.row input{padding:6px 10px;border:1px solid #e5e6eb;border-radius:6px;font-size:13px;outline:none}
.row input:focus{border-color:#4e83fd}
.btn{padding:8px 20px;border-radius:8px;border:none;font-size:14px;cursor:pointer;font-weight:500}
.btn-primary{background:#4e83fd;color:#fff}
.btn-primary:hover{background:#3b71ec}
.btn-primary:disabled{opacity:0.5;cursor:not-allowed}
.btn-secondary{background:#f0f1f5;color:#646a73}
.status-bar{padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:12px;display:none}
.status-bar.info{display:block;background:#e8f0fe;color:#1a56db}
.status-bar.error{display:block;background:#fde8e8;color:#d83931}
.status-bar.success{display:block;background:#e8f7e8;color:#1a7f1a}
.output-item{padding:8px 12px;border:1px solid #e5e6eb;border-radius:6px;margin-bottom:6px;font-size:13px;display:flex;justify-content:space-between}
pre{background:#f5f6f8;padding:12px;border-radius:6px;font-size:12px;line-height:1.6;white-space:pre-wrap;max-height:400px;overflow-y:auto;user-select:text}
.sig-pos{color:#1a7f1a;font-weight:600}
.sig-neg{color:#d83931;font-weight:600}
.loading{text-align:center;padding:30px;color:#bbbfc4}
.spinner{display:inline-block;width:20px;height:20px;border:2px solid #e5e6eb;border-top-color:#4e83fd;border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="container">
<h1>🧪 实验报告生成</h1>

<div class="card">
  <div class="card-title">实验参数</div>
  <div class="row">
    <label>Flight ID</label>
    <input type="number" id="flight-id" placeholder="输入实验 ID">
  </div>
  <div class="row">
    <label>版本名</label>
    <input type="text" id="version" placeholder="如 v1（可选，默认第一个实验组）">
  </div>
  <div class="row">
    <label>开始日期</label>
    <input type="text" id="start-date" placeholder="YYYY-MM-DD（可选）" style="width:120px">
  </div>
  <div class="row">
    <label>结束日期</label>
    <input type="text" id="end-date" placeholder="YYYY-MM-DD（可选）" style="width:120px">
  </div>
  <div class="row" style="margin-top:12px;gap:8px">
    <button class="btn btn-primary" onclick="runCrawl()">爬取指标</button>
    <button class="btn btn-secondary" onclick="fillTest()">填入测试数据</button>
  </div>
</div>

<div class="status-bar" id="status"></div>
<div id="results"></div>

<div class="card">
  <div class="card-title">历史输出</div>
  <div id="outputs"><div class="loading">加载中...</div></div>
</div>
</div>

<script>
const API = window.location.origin;

function fillTest() {
  document.getElementById('flight-id').value = '71879109';
  document.getElementById('version').value = '';
  document.getElementById('start-date').value = '2026-03-16';
  document.getElementById('end-date').value = '2026-03-19';
  showStatus('已填入测试数据', 'info');
}

function showStatus(msg, type) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status-bar ' + type;
}

async function runCrawl() {
  const fid = document.getElementById('flight-id').value;
  if (!fid) { showStatus('请输入 Flight ID', 'error'); return; }
  showStatus('正在爬取指标数据...', 'info');
  document.getElementById('results').innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    const body = { flight_id: parseInt(fid) };
    const v = document.getElementById('version').value;
    const sd = document.getElementById('start-date').value;
    const ed = document.getElementById('end-date').value;
    if (v) body.version = v;
    if (sd) body.start_date = sd;
    if (ed) body.end_date = ed;

    const res = await fetch(API + '/crawl', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail||'失败'); }
    const data = await res.json();
    showStatus('爬取完成: ' + data.output, 'success');
    renderCrawlResult(data.data);
    loadOutputs();
  } catch(e) {
    showStatus('爬取失败: ' + e.message, 'error');
    document.getElementById('results').innerHTML = '';
  }
}

function renderCrawlResult(data) {
  let html = '<div class="card"><div class="card-title">' + data.experiment_name + '</div>';
  html += '<div style="font-size:12px;color:#bbbfc4;margin-bottom:8px">';
  html += '对照: ' + data.base_vname + ' | 实验: ' + data.target_vname;
  html += ' | 日期: ' + data.start_date + ' ~ ' + data.end_date + '</div>';

  for (const [gid, g] of Object.entries(data.groups || {})) {
    if (g.error) { html += '<div style="color:#d83931;font-size:12px">❌ ' + g.group_name + ': ' + g.error + '</div>'; continue; }
    html += '<div style="font-size:12px;color:#646a73;margin-top:8px">[' + g.group_name + ']</div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px">';
    (g.metrics || []).forEach(m => {
      const rd = m.rel_diff;
      const sig = m.significant;
      let cls = '';
      if (sig && rd >= 0) cls = 'sig-pos';
      else if (sig && rd < 0) cls = 'sig-neg';
      const pct = rd !== null && rd !== undefined ? (rd >= 0 ? '+' : '') + (rd * 100).toFixed(2) + '%' : 'N/A';
      html += '<span style="padding:3px 8px;border-radius:4px;font-size:12px;background:#f5f6f8" class="' + cls + '">' + m.name + ': ' + pct + '</span>';
    });
    html += '</div>';
  }
  html += '</div>';
  document.getElementById('results').innerHTML = html;
}

async function loadOutputs() {
  try {
    const res = await fetch(API + '/outputs');
    const data = await res.json();
    const el = document.getElementById('outputs');
    if (!data.length) { el.innerHTML = '<div style="color:#bbbfc4;text-align:center;padding:12px">暂无输出</div>'; return; }
    el.innerHTML = data.map(o =>
      '<div class="output-item"><span>' + o.name + '</span><span style="color:#bbbfc4">' + o.screenshots + ' 截图, ' + (o.has_metrics ? '有数据' : '无数据') + '</span></div>'
    ).join('');
  } catch(e) { console.error(e); }
}

loadOutputs();
</script>
</body>
</html>"""
