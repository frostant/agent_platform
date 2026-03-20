"""心跳守护 — Agent 服务

定时检查所有 Agent 健康状态，异常自动通知 + 修复。

定时策略：
  工作日 9:30  → full 检查（test_e2e --live）
  工作日 22:00 → lite 检查（test_e2e --lite）
  周末 10:00   → lite 检查

API:
  GET  /health       自身健康检查
  POST /check        手动触发检查（{"mode": "lite"/"live"}）
  GET  /logs         查看健康记录
  GET  /status       各 Agent 最新状态汇总
  GET  /             前端看板
"""

import logging
import time
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from checker import run_check, get_health_logs

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent / "data"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "watchdog.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("watchdog")

# ---------------------------------------------------------------------------
# 定时调度
# ---------------------------------------------------------------------------

def _start_scheduler():
    """启动 APScheduler 定时任务"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("apscheduler 未安装，定时检查不可用")
        return

    scheduler = BackgroundScheduler()

    # 工作日 9:30 → full 检查
    scheduler.add_job(
        lambda: run_check("live"),
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30),
        id="weekday_morning",
        name="工作日晨检(full)",
    )

    # 工作日 22:00 → lite 检查
    scheduler.add_job(
        lambda: run_check("lite"),
        CronTrigger(day_of_week="mon-fri", hour=22, minute=0),
        id="weekday_evening",
        name="工作日晚检(lite)",
    )

    # 周末 10:00 → lite 检查
    scheduler.add_job(
        lambda: run_check("lite"),
        CronTrigger(day_of_week="sat,sun", hour=10, minute=0),
        id="weekend_morning",
        name="周末晨检(lite)",
    )

    scheduler.start()
    logger.info("定时调度已启动:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")

    # 启动后延迟 30 秒执行一次 lite 检查（开机首检）
    def boot_check():
        time.sleep(30)
        logger.info("开机首检(lite)...")
        run_check("lite")

    threading.Thread(target=boot_check, daemon=True).start()


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(title="心跳守护")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    _start_scheduler()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000)
    path = request.url.path
    if path != "/health":
        logger.info(f"{request.method} {path} → {response.status_code} ({duration}ms)")
    return response


class CheckRequest(BaseModel):
    mode: str = "lite"  # lite / live


@app.get("/health")
def health():
    logs = get_health_logs(1)
    last = logs[-1] if logs else None
    return {
        "status": "ok",
        "last_check": last.get("timestamp") if last else None,
        "last_result": "ok" if last and last.get("all_ok") else "issues" if last else "no_data",
    }


@app.post("/check")
def manual_check(req: CheckRequest):
    """手动触发健康检查"""
    logger.info(f"手动触发 {req.mode} 检查")
    result = run_check(req.mode)
    return result


@app.get("/logs")
def logs(limit: int = 50):
    return get_health_logs(limit)


@app.get("/status")
def status_summary():
    """各 Agent 最新状态汇总"""
    logs = get_health_logs(10)
    if not logs:
        return {"message": "暂无检查记录"}

    last = logs[-1]
    agents_status = {}
    for a in last.get("agents", []):
        agents_status[a["agent_id"]] = {
            "ok": a["ok"],
            "checks": [{c["check"]: c["ok"]} for c in a.get("checks", [])],
        }

    # 统计最近 10 次的可用率
    availability = {}
    for log in logs:
        for a in log.get("agents", []):
            aid = a["agent_id"]
            if aid not in availability:
                availability[aid] = {"total": 0, "ok": 0}
            availability[aid]["total"] += 1
            if a["ok"]:
                availability[aid]["ok"] += 1

    for aid in availability:
        t = availability[aid]["total"]
        o = availability[aid]["ok"]
        availability[aid]["rate"] = f"{o}/{t} ({o/t*100:.0f}%)" if t > 0 else "N/A"

    return {
        "last_check": last.get("timestamp"),
        "last_mode": last.get("mode"),
        "all_ok": last.get("all_ok"),
        "agents": agents_status,
        "availability": availability,
    }


# ---------------------------------------------------------------------------
# 前端看板
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>心跳守护</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7f8fa;color:#1f2329}
.container{max-width:800px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;font-weight:600;margin-bottom:16px}
.card{background:#fff;border-radius:10px;border:1px solid #e5e6eb;padding:16px;margin-bottom:12px}
.card-title{font-size:14px;font-weight:500;color:#646a73;margin-bottom:10px}
.btn{padding:8px 20px;border-radius:8px;border:none;font-size:14px;cursor:pointer;font-weight:500}
.btn-primary{background:#4e83fd;color:#fff}
.btn-primary:hover{background:#3b71ec}
.btn-primary:disabled{opacity:0.5;cursor:not-allowed}
.btn-secondary{background:#f0f1f5;color:#646a73}
.status-bar{padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:12px;display:none}
.status-bar.info{display:block;background:#e8f0fe;color:#1a56db}
.status-bar.error{display:block;background:#fde8e8;color:#d83931}
.status-bar.success{display:block;background:#e8f7e8;color:#1a7f1a}
.agent-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #f0f1f5;font-size:13px}
.agent-row:last-child{border:none}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.dot.ok{background:#1a7f1a}
.dot.fail{background:#d83931}
.dot.unknown{background:#bbbfc4}
.log-item{padding:8px;border-bottom:1px solid #f0f1f5;font-size:12px}
.log-item:last-child{border:none}
.log-time{color:#bbbfc4;margin-right:8px}
.avail{font-size:12px;color:#646a73;margin-left:8px}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #e5e6eb;border-top-color:#4e83fd;border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="container">
<h1>🛡️ 心跳守护</h1>

<div class="card">
  <div class="card-title">当前状态</div>
  <div id="agent-status"><div style="color:#bbbfc4">加载中...</div></div>
</div>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <div class="card-title" style="margin:0">操作</div>
  </div>
  <div style="display:flex;gap:8px">
    <button class="btn btn-primary" id="btn-lite" onclick="runCheck('lite')">Lite 检查</button>
    <button class="btn btn-secondary" onclick="runCheck('live')">Full 检查</button>
  </div>
</div>

<div class="status-bar" id="status"></div>

<div class="card">
  <div class="card-title">检查历史</div>
  <div id="logs"><div style="color:#bbbfc4">加载中...</div></div>
</div>
</div>

<script>
const API = window.location.origin;

function showStatus(msg, type) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status-bar ' + type;
}

async function loadStatus() {
  try {
    const res = await fetch(API + '/status');
    const data = await res.json();
    const el = document.getElementById('agent-status');

    if (data.message) { el.innerHTML = '<div style="color:#bbbfc4">' + data.message + '</div>'; return; }

    let html = '<div style="font-size:12px;color:#bbbfc4;margin-bottom:8px">上次检查: ' + (data.last_check||'无') + ' (' + (data.last_mode||'-') + ')</div>';
    for (const [aid, info] of Object.entries(data.agents || {})) {
      const dot = info.ok ? 'ok' : 'fail';
      const avail = data.availability && data.availability[aid] ? data.availability[aid].rate : '';
      html += '<div class="agent-row"><span><span class="dot ' + dot + '"></span>' + aid + '</span><span class="avail">' + avail + '</span></div>';
    }
    el.innerHTML = html;
  } catch(e) { console.error(e); }
}

async function loadLogs() {
  try {
    const res = await fetch(API + '/logs?limit=20');
    const data = await res.json();
    const el = document.getElementById('logs');
    if (!data.length) { el.innerHTML = '<div style="color:#bbbfc4">暂无记录</div>'; return; }
    el.innerHTML = data.reverse().map(log => {
      const icon = log.all_ok ? '✅' : '⚠️';
      const count = (log.agents||[]).length;
      const failed = (log.agents||[]).filter(a => !a.ok).map(a => a.agent_id).join(', ');
      return '<div class="log-item"><span class="log-time">' + log.timestamp.slice(0,19) + '</span>' + icon + ' ' + log.mode + ' | ' + count + ' agents' + (failed ? ' | ❌ ' + failed : '') + '</div>';
    }).join('');
  } catch(e) { console.error(e); }
}

async function runCheck(mode) {
  const btn = document.getElementById('btn-lite');
  btn.disabled = true;
  showStatus('正在执行 ' + mode + ' 检查...', 'info');
  try {
    const res = await fetch(API + '/check', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});
    const data = await res.json();
    if (data.all_ok) showStatus('检查完成: 全部正常', 'success');
    else showStatus('检查完成: 发现异常', 'error');
    loadStatus();
    loadLogs();
  } catch(e) {
    showStatus('检查失败: ' + e.message, 'error');
  }
  btn.disabled = false;
}

loadStatus();
loadLogs();
setInterval(loadStatus, 30000);
</script>
</body>
</html>"""
