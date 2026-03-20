"""每日实验摘要 — Agent 服务

复用 digest/ 模块（已验证的本地代码），包装为 FastAPI 服务 + 前端页面。

API:
  GET  /health          健康检查
  POST /experiment      单实验查询（支持自定义日期/地区）
  POST /digest          全量拉取（手动触发）
  GET  /                前端页面
"""

import logging
import time
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# digest 模块自包含：client/config/experiment/core 全在 digest/ 内
from digest import (
    LibraClient, COOKIES_PATH, LIBRA_FLIGHT_URL,
    load_digest_config, query_experiment,
    build_detail, build_summary_table, format_pct,
    fetch_running_experiments, filter_recent_experiments,
)
from digest.core import RETRY_WAITS

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent / "data"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "daily_digest.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("daily_digest")

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="每日实验摘要")

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
class ExperimentRequest(BaseModel):
    flight_id: int
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None
    data_region: Optional[str] = None  # EU/ROW/US, None=全局


class DigestRequest(BaseModel):
    data_region: Optional[str] = None


# ---------------------------------------------------------------------------
# API 接口
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "cookies_configured": COOKIES_PATH.exists()}


@app.post("/experiment")
def experiment(req: ExperimentRequest):
    """单实验查询：复用 digest.core.query_experiment()"""
    logger.info(f"单实验查询: flight_id={req.flight_id}, dates={req.start_date}~{req.end_date}, region={req.data_region}")

    if not COOKIES_PATH.exists():
        raise HTTPException(500, "cookies.json 不存在")

    try:
        client = LibraClient(COOKIES_PATH)
        config = load_digest_config()

        # 获取实验元信息
        meta = client.get_conclusion_report_meta(req.flight_id)
        exp_name = meta.get('experiment_name', f'实验 {req.flight_id}')
        start_time_ts = meta.get('start_time')

        # 调用核心查询函数
        result = query_experiment(
            client, req.flight_id, config, exp_name, start_time_ts,
            start_date=req.start_date, end_date=req.end_date,
            data_region=req.data_region,
        )

        # 补充 URL + 文本摘要
        result['url'] = LIBRA_FLIGHT_URL.format(flight_id=req.flight_id)
        if result.get('versions_results'):
            result['detail_text'] = build_detail(
                1, result['name'], req.flight_id,
                result['versions_results'], result.get('warnings', [])
            )

        logger.info(f"实验 {req.flight_id} 查询完成: status={result['status']}")
        return result

    except Exception as e:
        logger.error(f"实验查询失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"查询失败: {e}")


@app.post("/digest")
def digest(req: DigestRequest):
    """全量拉取：复用 digest.core 的函数组合"""
    logger.info(f"全量 digest: region={req.data_region}")

    if not COOKIES_PATH.exists():
        raise HTTPException(500, "cookies.json 不存在")

    try:
        client = LibraClient(COOKIES_PATH)
        config = load_digest_config()

        # 获取运行中实验
        experiments = fetch_running_experiments(client.session)
        logger.info(f"共 {len(experiments)} 个运行中实验")

        if not experiments:
            return _empty_digest_result()

        recent_exps, skipped = filter_recent_experiments(experiments)
        logger.info(f"筛选后: {len(recent_exps)} 个近期, 跳过 {skipped} 个长期")

        if not recent_exps:
            return _empty_digest_result(skipped=skipped)

        # 第一轮查询
        ok_results = []
        not_ready = []
        failed = []
        retry_queue = []

        for exp in recent_exps:
            fid = exp['id']
            ename = exp.get('name', '未知')
            start_ts = exp.get('start_time')

            result = query_experiment(
                client, fid, config, ename, start_ts,
                data_region=req.data_region,
            )
            result['url'] = LIBRA_FLIGHT_URL.format(flight_id=fid)
            if result.get('versions_results'):
                result['detail_text'] = build_detail(
                    len(ok_results) + 1, result['name'], fid,
                    result['versions_results'], result.get('warnings', [])
                )

            if result['status'] == 'ok':
                ok_results.append(result)
            elif result['status'] == 'computing':
                retry_queue.append((exp, result))
            elif result['status'] == 'fail':
                failed.append({'name': ename, 'flight_id': fid, 'error': result.get('error')})
            else:
                not_ready.append({'name': ename, 'flight_id': fid, 'reason': result.get('skip_reason', result['status'])})

        # 重试 computing
        if retry_queue:
            for attempt, wait in enumerate(RETRY_WAITS):
                logger.info(f"{len(retry_queue)} 个实验计算中，等待 {wait}s...")
                time.sleep(wait)
                still_computing = []
                for exp, _ in retry_queue:
                    fid = exp['id']
                    ename = exp.get('name', '未知')
                    start_ts = exp.get('start_time')
                    result = query_experiment(client, fid, config, ename, start_ts, data_region=req.data_region)
                    result['url'] = LIBRA_FLIGHT_URL.format(flight_id=fid)
                    if result['status'] == 'ok':
                        ok_results.append(result)
                    elif result['status'] == 'computing':
                        still_computing.append((exp, result))
                    elif result['status'] == 'fail':
                        failed.append({'name': ename, 'flight_id': fid, 'error': result.get('error')})
                    else:
                        not_ready.append({'name': ename, 'flight_id': fid, 'reason': result.get('skip_reason', result['status'])})
                retry_queue = still_computing
                if not retry_queue:
                    break

            for exp, _ in retry_queue:
                not_ready.append({'name': exp.get('name', '未知'), 'flight_id': exp['id'], 'reason': '重试后仍在计算中'})

        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')

        return {
            'date': today,
            'experiments': ok_results,
            'not_ready': not_ready,
            'failed': failed,
            'summary_stats': {
                'total': len(recent_exps),
                'ok': len(ok_results),
                'not_ready': len(not_ready),
                'failed': len(failed),
                'skipped': skipped,
            },
        }

    except Exception as e:
        logger.error(f"digest 失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"拉取失败: {e}")


def _empty_digest_result(skipped=0):
    from datetime import datetime
    return {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'experiments': [], 'not_ready': [], 'failed': [],
        'summary_stats': {'total': 0, 'ok': 0, 'not_ready': 0, 'failed': 0, 'skipped': skipped},
    }


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
<title>每日实验摘要</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7f8fa;color:#1f2329}
.container{max-width:960px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;font-weight:600;margin-bottom:16px}
.tabs{display:flex;gap:0;margin-bottom:16px}
.tab{padding:8px 20px;border:1px solid #e5e6eb;background:#fff;cursor:pointer;font-size:13px;color:#646a73}
.tab:first-child{border-radius:8px 0 0 8px}
.tab:last-child{border-radius:0 8px 8px 0}
.tab.active{background:#4e83fd;color:#fff;border-color:#4e83fd}
.card{background:#fff;border-radius:10px;border:1px solid #e5e6eb;padding:16px;margin-bottom:12px}
.card-title{font-size:14px;font-weight:500;color:#646a73;margin-bottom:10px}
.row{display:flex;gap:10px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
.row label{font-size:13px;color:#646a73;min-width:60px}
.row input,.row select{padding:6px 10px;border:1px solid #e5e6eb;border-radius:6px;font-size:13px;outline:none}
.row input:focus,.row select:focus{border-color:#4e83fd}
.btn{padding:8px 20px;border-radius:8px;border:none;font-size:14px;cursor:pointer;font-weight:500}
.btn-primary{background:#4e83fd;color:#fff}
.btn-primary:hover{background:#3b71ec}
.btn-primary:disabled{opacity:0.5;cursor:not-allowed}
.status-bar{padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:12px;display:none}
.status-bar.info{display:block;background:#e8f0fe;color:#1a56db}
.status-bar.error{display:block;background:#fde8e8;color:#d83931}
.status-bar.success{display:block;background:#e8f7e8;color:#1a7f1a}
.summary-table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
.summary-table th{background:#f5f6f8;padding:8px 10px;text-align:left;font-weight:500;color:#646a73;border-bottom:2px solid #e5e6eb}
.summary-table td{padding:8px 10px;border-bottom:1px solid #f0f1f5}
.summary-table tr:hover{background:#f9fafb}
.sig-pos{color:#1a7f1a;font-weight:600}
.sig-neg{color:#d83931;font-weight:600}
.na{color:#bbbfc4}
.exp-card{border:1px solid #e5e6eb;border-radius:8px;padding:12px;margin-bottom:8px;background:#fff}
.exp-card h3{font-size:14px;margin-bottom:4px}
.exp-card a{color:#4e83fd;font-size:12px;text-decoration:none}
.exp-card a:hover{text-decoration:underline}
.metric-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.metric-chip{padding:3px 8px;border-radius:4px;font-size:12px;background:#f5f6f8}
.metric-chip.pos{background:#e8f7e8;color:#1a7f1a}
.metric-chip.neg{background:#fde8e8;color:#d83931}
.loading{text-align:center;padding:40px;color:#bbbfc4}
.spinner{display:inline-block;width:20px;height:20px;border:2px solid #e5e6eb;border-top-color:#4e83fd;border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="container">
<h1>📊 每日实验摘要</h1>

<div class="tabs">
  <div class="tab active" onclick="switchTab('all')">全量拉取</div>
  <div class="tab" onclick="switchTab('single')">单实验查询</div>
</div>

<!-- 全量拉取 -->
<div id="tab-all">
  <div class="card">
    <div class="card-title">参数设置</div>
    <div class="row">
      <label>地区</label>
      <select id="region-all">
        <option value="">全局（默认）</option>
        <option value="ROW">ROW</option>
        <option value="US">US</option>
        <option value="EU">EU</option>
      </select>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="btn btn-primary" id="btn-digest" onclick="runDigest()">拉取全部实验</button>
    </div>
  </div>
</div>

<!-- 单实验查询 -->
<div id="tab-single" style="display:none">
  <div class="card">
    <div class="card-title">实验参数</div>
    <div class="row">
      <label>Flight ID</label>
      <input type="number" id="flight-id" placeholder="输入实验 ID">
    </div>
    <div class="row">
      <label>地区</label>
      <select id="region-single">
        <option value="">全局（默认）</option>
        <option value="ROW" selected>ROW</option>
        <option value="US">US</option>
        <option value="EU">EU</option>
      </select>
    </div>
    <div class="row">
      <label>开始日期</label>
      <input type="text" id="start-date-single" placeholder="YYYY-MM-DD" style="width:120px">
      <span style="font-size:12px;color:#bbbfc4">可选，覆盖自动计算</span>
    </div>
    <div class="row">
      <label>结束日期</label>
      <input type="text" id="end-date-single" placeholder="YYYY-MM-DD" style="width:120px">
      <span style="font-size:12px;color:#bbbfc4">可选，默认 T-2</span>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="btn btn-primary" id="btn-single" onclick="runSingle()">查询实验</button>
      <button class="btn" style="background:#f0f1f5;color:#646a73" onclick="fillTestData()">填入测试数据</button>
    </div>
  </div>
</div>

<div class="status-bar" id="status"></div>
<div id="results"></div>
</div>

<script>
const API = window.location.origin;

// 初始化日期为当前
(function initDates() {
  const fmt = d => d.toISOString().slice(0, 10);
  const now = new Date();
  const end = new Date(now); end.setDate(end.getDate() - 2);
  const start = new Date(now); start.setDate(start.getDate() - 7);
  document.getElementById('start-date-single').value = fmt(start);
  document.getElementById('end-date-single').value = fmt(end);
})();

function fillTestData() {
  document.getElementById('flight-id').value = '71879109';
  document.getElementById('start-date-single').value = '2026-03-16';
  document.getElementById('end-date-single').value = '2026-03-19';
  document.getElementById('region-single').value = 'ROW';
  showStatus('已填入测试数据', 'info');
}

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', (i===0&&tab==='all')||(i===1&&tab==='single')));
  document.getElementById('tab-all').style.display = tab==='all'?'block':'none';
  document.getElementById('tab-single').style.display = tab==='single'?'block':'none';
}

function showStatus(msg, type) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status-bar ' + type;
}

function formatPct(rd, cf) {
  if (rd === null || rd === undefined) return '<span class="na">N/A</span>';
  const pct = (rd * 100).toFixed(2);
  const sign = rd >= 0 ? '+' : '';
  if (cf === 1 || cf === -1) {
    // 显著：按 rel_diff 原始值的正负决定颜色
    const cls = rd >= 0 ? 'sig-pos' : 'sig-neg';
    return '<span class="' + cls + '">' + sign + pct + '%</span>';
  }
  return sign + pct + '%';
}

async function runDigest() {
  const btn = document.getElementById('btn-digest');
  btn.disabled = true; btn.textContent = '拉取中...';
  showStatus('正在拉取所有实验数据，可能需要数分钟...', 'info');
  document.getElementById('results').innerHTML = '<div class="loading"><div class="spinner"></div><p style="margin-top:8px">正在连接 Libra...</p></div>';
  try {
    const body = {};
    const region = document.getElementById('region-all').value;
    if (region) body.data_region = region;
    const res = await fetch(API + '/digest', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || '请求失败'); }
    const data = await res.json();
    showStatus('拉取完成: ' + data.summary_stats.ok + ' 个实验有数据', 'success');
    renderDigestResults(data);
  } catch(e) {
    showStatus('拉取失败: ' + e.message, 'error');
    document.getElementById('results').innerHTML = '';
  }
  btn.disabled = false; btn.textContent = '拉取全部实验';
}

async function runSingle() {
  const flightId = document.getElementById('flight-id').value;
  if (!flightId) { showStatus('请输入 Flight ID', 'error'); return; }
  const btn = document.getElementById('btn-single');
  btn.disabled = true; btn.textContent = '查询中...';
  showStatus('正在查询实验 ' + flightId + '...', 'info');
  document.getElementById('results').innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    const body = { flight_id: parseInt(flightId) };
    const region = document.getElementById('region-single').value;
    if (region) body.data_region = region;
    const sd = document.getElementById('start-date-single').value;
    const ed = document.getElementById('end-date-single').value;
    if (sd) body.start_date = sd;
    if (ed) body.end_date = ed;
    const res = await fetch(API + '/experiment', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || '请求失败'); }
    const data = await res.json();
    showStatus('查询完成: ' + data.name + ' (status=' + data.status + ')', 'success');
    renderSingleResult(data);
  } catch(e) {
    showStatus('查询失败: ' + e.message, 'error');
    document.getElementById('results').innerHTML = '';
  }
  btn.disabled = false; btn.textContent = '查询实验';
}

function renderDigestResults(data) {
  const el = document.getElementById('results');
  let html = '<div class="card"><div class="card-title">摘要 (' + data.date + ')</div>';
  const s = data.summary_stats;
  html += '<div style="font-size:13px;color:#646a73;margin-bottom:8px">';
  html += '共 ' + s.total + ' 个实验，' + s.ok + ' 个有数据，' + s.not_ready + ' 个未就绪，' + s.skipped + ' 个长期跳过</div>';

  if (data.experiments.length > 0) {
    // 收集所有 short 列头
    const allShorts = [];
    const first = data.experiments[0];
    if (first.versions_results && first.versions_results[0]) {
      first.versions_results[0][1].forEach(g => {
        g.metrics.forEach(m => { if(m.short) allShorts.push(m.short); });
      });
    }
    if (allShorts.length > 0) {
      html += '<div style="overflow-x:auto"><table class="summary-table"><thead><tr><th>实验</th><th>日期</th>';
      allShorts.forEach(s => html += '<th>' + s + '</th>');
      html += '</tr></thead><tbody>';
      data.experiments.forEach(exp => {
        (exp.versions_results || []).forEach((vr,vi) => {
          const vname = vr[0], groups = vr[1];
          const multi = exp.versions_results.length > 1;
          html += '<tr><td>' + (vi===0 ? exp.name : '') + (multi ? ' (' + vname + ')' : '') + '</td>';
          html += '<td style="font-size:12px;color:#bbbfc4">' + (vi===0?exp.start_date:'') + '</td>';
          const mmap = {};
          groups.forEach(g => g.metrics.forEach(m => { if(m.short) mmap[m.short]=m; }));
          allShorts.forEach(s => {
            const m = mmap[s];
            html += '<td>' + (m ? formatPct(m.rel_diff, m.confidence) : '<span class="na">-</span>') + '</td>';
          });
          html += '</tr>';
        });
      });
      html += '</tbody></table></div>';
    }
  }
  html += '</div>';
  data.experiments.forEach(exp => { html += renderExpCard(exp); });
  if (data.not_ready.length > 0) {
    html += '<div class="card"><div class="card-title">⏳ 数据未就绪 (' + data.not_ready.length + ')</div>';
    data.not_ready.forEach(nr => {
      html += '<div style="font-size:13px;padding:4px 0">• ' + nr.name + ' (id=' + nr.flight_id + '): ' + nr.reason + '</div>';
    });
    html += '</div>';
  }
  // 纯文本输出
  if (data.experiments.length > 0) {
    html += renderTextOutput(data.experiments);
  }

  el.innerHTML = html;
}

function renderSingleResult(data) {
  document.getElementById('results').innerHTML = renderExpCard(data) + renderTextOutput(data);
}

function formatPctText(rd, cf) {
  if (rd === null || rd === undefined) return 'N/A';
  const pct = (rd * 100).toFixed(2);
  const sign = rd >= 0 ? '+' : '';
  const isSig = cf === 1 || cf === -1;
  const emoji = isSig ? (rd >= 0 ? '🟢' : '🔴') : '';
  return emoji + sign + pct + '%';
}

function expToText(exp) {
  let lines = [];
  lines.push(exp.name || '实验 ' + exp.flight_id);
  if (exp.url) lines.push(exp.url);
  lines.push('日期: ' + exp.start_date + ' ~ ' + exp.end_date + '  状态: ' + exp.status);
  if (exp.skip_reason) lines.push('⚠ ' + exp.skip_reason);
  if (exp.error) lines.push('❌ ' + exp.error);
  if (exp.warnings && exp.warnings.length) lines.push('⚠ ' + exp.warnings.join('; '));
  (exp.versions_results || []).forEach(vr => {
    const vname = vr[0], groups = vr[1];
    if (exp.versions_results.length > 1) lines.push('--- ' + vname + ' ---');
    (groups || []).forEach(g => {
      lines.push('[' + g.group_name + ']');
      const parts = g.metrics.map(m => (m.short||m.name) + ' ' + formatPctText(m.rel_diff, m.confidence));
      lines.push('  ' + parts.join(', '));
    });
  });
  return lines.join('\\n');
}

function colorizeLibraLine(text) {
  // 给 libra: 行中的百分比数字加颜色
  // +1.14% → 绿色, -0.01% → 红色, 0.00% → 黑色
  return text.replace(/([+-]?\d+\.\d+%)/g, function(match) {
    const val = parseFloat(match);
    if (val > 0) return '<span style="color:#1a7f1a;font-weight:600">' + match + '</span>';
    if (val < 0) return '<span style="color:#d83931;font-weight:600">' + match + '</span>';
    return match;
  });
}

function renderTextOutput(data) {
  let rawText;
  if (Array.isArray(data)) {
    rawText = data.map(d => d.detail_text || expToText(d)).join('\\n\\n');
  } else {
    rawText = data.detail_text || expToText(data);
  }
  // 逐行处理：libra: 行加颜色，其他行也加颜色标记
  const colorized = rawText.split('\\n').map(line => {
    let safe = line.replace(/</g,'&lt;').replace(/>/g,'&gt;');
    // 🟢 🔴 emoji 也转成颜色
    safe = safe.replace(/🟢([+-]?\d+\.\d+%)/g, '<span style="color:#1a7f1a;font-weight:600">$1</span>');
    safe = safe.replace(/🔴([+-]?\d+\.\d+%)/g, '<span style="color:#d83931;font-weight:600">$1</span>');
    // libra: 行的百分比加颜色
    if (safe.startsWith('libra:')) {
      safe = colorizeLibraLine(safe);
    }
    return safe;
  }).join('\\n');

  return '<div class="card" style="margin-top:8px">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
    '<div class="card-title" style="margin:0">摘要输出（可复制）</div>' +
    '<button class="btn" style="background:#f0f1f5;color:#646a73;padding:4px 12px;font-size:12px" onclick="copyText()">复制</button>' +
    '</div>' +
    '<div id="text-output" style="background:#f5f6f8;padding:12px;border-radius:6px;font-size:12px;line-height:1.8;white-space:pre-wrap;word-break:break-all;max-height:400px;overflow-y:auto;user-select:text;cursor:text">' +
    colorized +
    '</div></div>';
}

function copyText() {
  const el = document.getElementById('text-output');
  if (!el) return;
  navigator.clipboard.writeText(el.innerText).then(() => {
    showStatus('已复制到剪贴板', 'success');
  }).catch(() => {
    // fallback
    const range = document.createRange();
    range.selectNodeContents(el);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    document.execCommand('copy');
    showStatus('已复制到剪贴板', 'success');
  });
}

function renderExpCard(exp) {
  let html = '<div class="exp-card">';
  html += '<h3>' + (exp.name || '实验 ' + exp.flight_id) + '</h3>';
  if (exp.url) html += '<a href="' + exp.url + '" target="_blank">' + exp.url + '</a>';
  html += '<div style="font-size:12px;color:#bbbfc4;margin-top:4px">日期: ' + exp.start_date + ' ~ ' + exp.end_date + ' | 状态: ' + exp.status + '</div>';
  if (exp.skip_reason) html += '<div style="font-size:12px;color:#e67700;margin-top:4px">⚠ ' + exp.skip_reason + '</div>';
  if (exp.error) html += '<div style="font-size:12px;color:#d83931;margin-top:4px">❌ ' + exp.error + '</div>';
  if (exp.warnings && exp.warnings.length) {
    html += '<div style="font-size:12px;color:#e67700;margin-top:4px">⚠ ' + exp.warnings.join('; ') + '</div>';
  }
  (exp.versions_results || []).forEach(vr => {
    const vname = vr[0], groups = vr[1];
    if (exp.versions_results.length > 1) html += '<div style="font-size:13px;font-weight:500;margin-top:8px">' + vname + '</div>';
    (groups || []).forEach(g => {
      html += '<div style="font-size:12px;color:#646a73;margin-top:6px">[' + g.group_name + ']</div>';
      html += '<div class="metric-row">';
      g.metrics.forEach(m => {
        const isSig = m.confidence === 1 || m.confidence === -1;
        const cls = isSig ? (m.rel_diff >= 0 ? ' pos' : ' neg') : '';
        html += '<div class="metric-chip' + cls + '">' + (m.short||m.name) + ': ' + formatPct(m.rel_diff, m.confidence) + '</div>';
      });
      html += '</div>';
    });
  });
  html += '</div>';
  return html;
}
</script>
</body>
</html>"""
