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

# launch_report 是自包含包，通过包导入
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
from launch_report.libra_sdk.client import LibraClient
from launch_report.libra_sdk.experiment import ExperimentHelper
from launch_report.libra_sdk.screenshot_parallel import capture_screenshots_parallel
from launch_report.config import load_metrics3_config, load_settings, get_launch_report_groups
from launch_report.crawl_metrics import crawl as do_crawl
from launch_report.report.generator import ReportGenerator
from launch_report.feishu_sdk import FeishuDoc

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
    datacenter: Optional[str] = "ROW"  # 命名用，与截图目录对齐


class ScreenshotRequest(BaseModel):
    flight_id: int
    version: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    datacenter: Optional[str] = "ROW"  # ROW/EU/None
    group_ids: Optional[List[int]] = None  # 指定截图的指标组 ID，None=全部
    max_workers: int = 4


class GenerateRequest(BaseModel):
    flight_id: int
    version: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    datacenter: Optional[str] = "ROW"
    doc_id: Optional[str] = None  # 已有飞书文档 ID，None=新建
    test_mode: bool = True  # 文档首行加时间戳


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
    """爬取指标数据 — 直接复用 crawl_metrics.crawl()"""
    logger.info(f"爬取指标: flight_id={req.flight_id}, version={req.version}")

    if not COOKIES_PATH.exists():
        raise HTTPException(500, "cookies.json 不存在")

    try:
        # 确定版本名：未指定时取第一个实验组
        target_version = req.version
        if not target_version:
            client = LibraClient(COOKIES_PATH)
            baseuser_data = client.get_baseuser(req.flight_id)
            info = ExperimentHelper.identify_base_version(baseuser_data.get('baseuser', []))
            if not info['exp_versions']:
                raise HTTPException(400, "无实验组版本")
            target_version = info['exp_versions'][0][1]
            logger.info(f"未指定版本，使用第一个实验组: {target_version}")

        # 输出目录（统一命名，datacenter 与截图对齐）
        out_dir = _make_output_dir(req.flight_id, target_version, req.datacenter, req.start_date, req.end_date)

        # 复用已验证的 crawl 函数
        result = do_crawl(
            flight_id=req.flight_id,
            target_version=target_version,
            output_dir=out_dir,
            start_date=req.start_date,
            end_date=req.end_date,
            cookies_path=COOKIES_PATH,
        )

        logger.info(f"爬取完成: {out_dir}")
        return {"ok": True, "output": out_dir, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"爬取失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"爬取失败: {e}")


def _make_output_dir(flight_id, version, datacenter=None, start_date=None, end_date=None):
    """统一命名规范：{flight_id}_{version}_{datacenter}_{start}_{end}
    相同参数覆盖而非新建"""
    parts = [str(flight_id)]
    parts.append(version or "default")
    parts.append(datacenter or "ALL")
    if start_date:
        parts.append(start_date)
    if end_date:
        parts.append(end_date)
    name = "_".join(parts)
    out = OUTPUT_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    return str(out)


def _resolve_version_info(flight_id, version_name=None):
    """获取实验的版本信息，返回 (base_vid, base_vname, target_vid, target_vname, start_date, end_date)"""
    client = LibraClient(COOKIES_PATH)
    meta = client.get_conclusion_report_meta(flight_id)
    baseuser_data = client.get_baseuser(flight_id)
    info = ExperimentHelper.identify_base_version(baseuser_data.get('baseuser', []))

    if not info['exp_versions']:
        raise HTTPException(400, "无实验组版本")

    # 找目标版本
    target = None
    for vid, vname, users in info['exp_versions']:
        if version_name and vname == version_name:
            target = (vid, vname)
            break
    if not target:
        target = (info['exp_versions'][0][0], info['exp_versions'][0][1])

    # 日期
    start_date, end_date, _ = ExperimentHelper.compute_date_range(baseuser_data, meta)

    return {
        'base_vid': info['base_vid'],
        'base_vname': info['base_vname'],
        'target_vid': target[0],
        'target_vname': target[1],
        'start_date': start_date,
        'end_date': end_date,
        'exp_name': meta.get('experiment_name', f'实验 {flight_id}'),
    }


@app.post("/screenshot")
def screenshot(req: ScreenshotRequest):
    """Step 1: Playwright 截图指标组"""
    logger.info(f"截图: flight_id={req.flight_id}, version={req.version}, groups={req.group_ids}")

    if not COOKIES_PATH.exists():
        raise HTTPException(500, "cookies.json 不存在")

    try:
        vi = _resolve_version_info(req.flight_id, req.version)
        start_date = req.start_date or vi['start_date']
        end_date = req.end_date or vi['end_date']
        target_vname = vi['target_vname']

        # 准备截图组
        config = load_metrics3_config()
        groups = get_launch_report_groups(config)
        # 补充 age_dimension
        for mg in config['metric_groups']:
            for g in groups:
                if g['group_id'] == mg['group_id']:
                    g['age_dimension'] = mg.get('age_dimension', 'predicted_age_group')

        # 按 group_ids 过滤
        if req.group_ids:
            groups = [g for g in groups if g['group_id'] in req.group_ids]

        out_dir = _make_output_dir(req.flight_id, target_vname, req.datacenter, start_date, end_date)

        logger.info(f"截图 {len(groups)} 个指标组 → {out_dir}")
        logger.info(f"  base_vid={vi['base_vid']}, target_vid={vi['target_vid']}, dates={start_date}~{end_date}")

        results = asyncio.run(capture_screenshots_parallel(
            flight_id=req.flight_id,
            groups=groups,
            output_dir=out_dir,
            datacenter=req.datacenter,
            max_workers=req.max_workers,
            start_date=start_date,
            end_date=end_date,
            base_vid=vi['base_vid'],
            target_vid=vi['target_vid'],
        ))

        # 统计结果
        total_screenshots = sum(len(r.get('files', [])) for r in results if isinstance(r, dict))
        logger.info(f"截图完成: {total_screenshots} 张")

        return {
            "ok": True,
            "output": out_dir,
            "groups": len(groups),
            "screenshots": total_screenshots,
            "details": results,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"截图失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"截图失败: {e}")


@app.post("/generate")
def generate(req: GenerateRequest):
    """完整端到端流程: 截图 → 爬取 → 生成飞书文档"""
    logger.info(f"生成报告: flight_id={req.flight_id}, version={req.version}, doc_id={req.doc_id}")

    if not COOKIES_PATH.exists():
        raise HTTPException(500, "cookies.json 不存在")

    try:
        vi = _resolve_version_info(req.flight_id, req.version)
        target_vname = vi['target_vname']
        start_date = req.start_date or vi['start_date']
        end_date = req.end_date or vi['end_date']

        out_dir = _make_output_dir(req.flight_id, target_vname, req.datacenter, start_date, end_date)
        out_path = Path(out_dir)

        # 检查缓存：已有截图和数据则跳过
        existing_screenshots = list(out_path.glob('*.png'))
        has_metrics = (out_path / 'metrics_data.json').exists()

        # Step 1: 截图（有截图则跳过）
        if existing_screenshots:
            logger.info(f"Step 1: 跳过截图（已有 {len(existing_screenshots)} 张）")
        else:
            logger.info("Step 1: 截图...")
            config = load_metrics3_config()
            groups = get_launch_report_groups(config)
            for mg in config['metric_groups']:
                for g in groups:
                    if g['group_id'] == mg['group_id']:
                        g['age_dimension'] = mg.get('age_dimension', 'predicted_age_group')

            asyncio.run(capture_screenshots_parallel(
                flight_id=req.flight_id,
                groups=groups,
                output_dir=out_dir,
                datacenter=req.datacenter,
                max_workers=4,
                start_date=start_date,
                end_date=end_date,
                base_vid=vi['base_vid'],
                target_vid=vi['target_vid'],
            ))

        # Step 2: 爬取数据（有 metrics_data.json 则跳过）
        if has_metrics:
            logger.info("Step 2: 跳过爬取（已有 metrics_data.json）")
        else:
            logger.info("Step 2: 爬取指标...")
            do_crawl(
                flight_id=req.flight_id,
                target_version=target_vname,
                output_dir=out_dir,
                start_date=start_date,
                end_date=end_date,
                cookies_path=COOKIES_PATH,
            )

        # Step 3: 生成飞书文档
        logger.info("Step 3: 生成飞书文档...")
        gen = ReportGenerator(
            flight_id=req.flight_id,
            target_version=target_vname,
            screenshots_dir=out_dir,
        )
        gen.prepare()

        doc = FeishuDoc(req.doc_id)
        doc.auth()

        if not req.doc_id:
            title = f"[Launch Notice] {vi['exp_name']} - {target_vname}"
            doc.create_document(title)

        gen.render(doc, test_mode=req.test_mode)

        try:
            doc.set_public_permission("tenant_readable")
        except Exception as e:
            logger.warning(f"设置文档权限失败: {e}")

        doc_url = f"https://bytedance.larkoffice.com/docx/{doc.document_id}" if doc.document_id else None
        logger.info(f"报告生成完成: {doc_url}")

        return {
            "ok": True,
            "output": out_dir,
            "doc_id": doc.document_id,
            "doc_url": doc_url,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"报告生成失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"报告生成失败: {e}")


@app.get("/progress")
def progress(dir_name: str = ""):
    """查询截图进度：返回指定目录下的 png 文件数量"""
    if not dir_name:
        return {"current": 0, "total": 21}
    target = OUTPUT_DIR / dir_name
    if not target.exists():
        return {"current": 0, "total": 21, "dir": dir_name}
    pngs = list(target.glob("*.png"))
    return {"current": len(pngs), "total": 21, "dir": dir_name}


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
  <div class="row">
    <label>机房</label>
    <select id="datacenter" style="padding:6px 10px;border:1px solid #e5e6eb;border-radius:6px;font-size:13px">
      <option value="ROW" selected>ROW</option>
      <option value="EU">EU</option>
      <option value="">全部</option>
    </select>
  </div>
  <div class="row" style="margin-top:12px;gap:8px">
    <button class="btn" style="background:#e8f0fe;color:#1a56db" onclick="runScreenshot()">1. 截图</button>
    <button class="btn btn-primary" onclick="runCrawl()">2. 爬取指标</button>
    <button class="btn" style="background:#e8f7e8;color:#1a7f1a" onclick="runGenerate()">3. 生成报告</button>
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
  const body = getParams();
  if (!body.flight_id) { showStatus('请输入 Flight ID', 'error'); return; }
  showStatus('正在爬取指标数据...', 'info');
  document.getElementById('results').innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
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

function getParams() {
  const body = {};
  const fid = document.getElementById('flight-id').value;
  if (fid) body.flight_id = parseInt(fid);
  const v = document.getElementById('version').value;
  const sd = document.getElementById('start-date').value;
  const ed = document.getElementById('end-date').value;
  const dc = document.getElementById('datacenter').value;
  if (v) body.version = v;
  if (sd) body.start_date = sd;
  if (ed) body.end_date = ed;
  if (dc) body.datacenter = dc;
  return body;
}

async function runScreenshot() {
  const body = getParams();
  if (!body.flight_id) { showStatus('请输入 Flight ID', 'error'); return; }
  showStatus('正在截图，可能需要数分钟...', 'info');

  // 计算输出目录名（和后端 _make_output_dir 一致）
  const dirName = [body.flight_id, body.version||'default', body.datacenter||'ALL', body.start_date||'', body.end_date||''].filter(Boolean).join('_');

  // 进度条 UI
  document.getElementById('results').innerHTML =
    '<div class="card">' +
    '<div class="card-title">截图进度</div>' +
    '<div style="background:#f0f1f5;border-radius:6px;height:24px;overflow:hidden;margin-bottom:8px">' +
    '<div id="progress-bar" style="background:#4e83fd;height:100%;width:0%;transition:width 0.5s;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;min-width:40px"></div>' +
    '</div>' +
    '<div id="progress-text" style="font-size:13px;color:#646a73;text-align:center">准备中...</div>' +
    '</div>';

  // 轮询进度
  let pollTimer = setInterval(async () => {
    try {
      const r = await fetch(API + '/progress?dir_name=' + encodeURIComponent(dirName));
      const p = await r.json();
      const pct = Math.round(p.current / p.total * 100);
      document.getElementById('progress-bar').style.width = pct + '%';
      document.getElementById('progress-bar').textContent = p.current + '/' + p.total;
      document.getElementById('progress-text').textContent = '已完成 ' + p.current + ' / ' + p.total + ' 张截图';
    } catch(e) {}
  }, 3000);

  try {
    const res = await fetch(API + '/screenshot', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    clearInterval(pollTimer);
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail||'失败'); }
    const data = await res.json();

    // 完成状态
    document.getElementById('progress-bar').style.width = '100%';
    document.getElementById('progress-bar').textContent = data.screenshots + '/' + data.screenshots;
    document.getElementById('progress-bar').style.background = '#1a7f1a';
    document.getElementById('progress-text').textContent = '截图完成: ' + data.screenshots + ' 张';
    showStatus('截图完成: ' + data.screenshots + ' 张', 'success');
    loadOutputs();
  } catch(e) {
    clearInterval(pollTimer);
    showStatus('截图失败: ' + e.message, 'error');
    document.getElementById('results').innerHTML = '';
  }
}

async function runGenerate() {
  const body = getParams();
  if (!body.flight_id) { showStatus('请输入 Flight ID', 'error'); return; }
  body.test_mode = true;
  showStatus('正在生成报告（截图→爬取→飞书文档），可能需要数分钟...', 'info');

  const dirName2 = [body.flight_id, body.version||'default', body.datacenter||'ALL', body.start_date||'', body.end_date||''].filter(Boolean).join('_');
  document.getElementById('results').innerHTML =
    '<div class="card"><div class="card-title">生成进度</div>' +
    '<div style="background:#f0f1f5;border-radius:6px;height:24px;overflow:hidden;margin-bottom:8px">' +
    '<div id="gen-bar" style="background:#4e83fd;height:100%;width:0%;transition:width 0.5s;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;min-width:40px"></div></div>' +
    '<div id="gen-text" style="font-size:13px;color:#646a73;text-align:center">准备中...</div></div>';

  let genPoll = setInterval(async () => {
    try {
      const r = await fetch(API + '/progress?dir_name=' + encodeURIComponent(dirName2));
      const p = await r.json();
      const pct = Math.round(p.current / p.total * 100);
      document.getElementById('gen-bar').style.width = pct + '%';
      document.getElementById('gen-bar').textContent = p.current + '/' + p.total;
      document.getElementById('gen-text').textContent = 'Step 1 截图: ' + p.current + '/' + p.total + ' 张';
    } catch(e) {}
  }, 3000);
  try {
    const res = await fetch(API + '/generate', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    clearInterval(genPoll);
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail||'失败'); }
    const data = await res.json();
    let html = '<div class="card"><div class="card-title">报告生成完成</div>';
    if (data.doc_url) html += '<div style="margin-bottom:8px"><a href="' + data.doc_url + '" target="_blank" style="color:#4e83fd">' + data.doc_url + '</a></div>';
    html += '<div style="font-size:13px;color:#646a73">输出目录: ' + data.output + '</div></div>';
    showStatus('报告生成完成', 'success');
    document.getElementById('results').innerHTML = html;
    loadOutputs();
  } catch(e) {
    clearInterval(genPoll);
    showStatus('报告生成失败: ' + e.message, 'error');
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
