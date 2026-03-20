"""飞书群消息推送 — Agent 服务

支持纯文本和富文本（post 类型）消息发送。
提供独立前端页面用于编辑和发送消息。
"""

import os
import json
import time
import hashlib
import base64
import hmac
import logging
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "data"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "feishu_notify.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("feishu_notify")

# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def _load_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value

_load_env()

WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("FEISHU_WEBHOOK_SECRET", "")

# ---------------------------------------------------------------------------
# 飞书发送逻辑
# ---------------------------------------------------------------------------

def _generate_sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _send_payload(webhook_url: str, payload: dict, secret: str = "") -> dict:
    """通用发送：支持任意 payload"""
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _generate_sign(ts, secret)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    logger.debug(f"发送 payload: {data.decode()[:500]}")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.read().decode("utf-8", errors="replace")}
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def _send_text(webhook_url: str, text: str, secret: str = "") -> dict:
    return _send_payload(webhook_url, {
        "msg_type": "text",
        "content": {"text": text},
    }, secret)


def _send_rich(webhook_url: str, title: str, content: list, secret: str = "") -> dict:
    """发送富文本（post 类型）消息
    content 格式: [[{tag, text/href, ...}, ...], ...]  每个子列表是一行
    """
    return _send_payload(webhook_url, {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": content,
                }
            }
        },
    }, secret)

# ---------------------------------------------------------------------------
# FastAPI 服务
# ---------------------------------------------------------------------------

app = FastAPI(title="飞书群消息推送")

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


class SendRequest(BaseModel):
    text: str


class RichTextElement(BaseModel):
    tag: str  # text, a, at, img
    text: Optional[str] = None
    href: Optional[str] = None
    user_id: Optional[str] = None
    style: Optional[List[str]] = None  # bold, underline, lineThrough, italic


class RichSendRequest(BaseModel):
    title: str = ""
    content: List[List[RichTextElement]]  # 二维数组，每个子数组是一行


class SendResponse(BaseModel):
    ok: bool
    message: str


@app.get("/health")
def health():
    logger.debug("健康检查")
    return {"status": "ok", "configured": bool(WEBHOOK_URL)}


@app.post("/send", response_model=SendResponse)
def send(req: SendRequest):
    if not WEBHOOK_URL:
        logger.error("发送失败：WEBHOOK_URL 未配置")
        raise HTTPException(500, "FEISHU_WEBHOOK_URL 未配置")
    logger.info(f"发送纯文本：{req.text[:80]}{'...' if len(req.text) > 80 else ''}")
    result = _send_text(WEBHOOK_URL, req.text, WEBHOOK_SECRET)
    if result.get("code") == 0:
        logger.info(f"发送成功（{len(req.text)} 字符）")
        return SendResponse(ok=True, message=f"已发送（{len(req.text)} 字符）")
    logger.error(f"发送失败：{result}")
    return SendResponse(ok=False, message=str(result))


@app.post("/send_rich", response_model=SendResponse)
def send_rich(req: RichSendRequest):
    if not WEBHOOK_URL:
        logger.error("发送富文本失败：WEBHOOK_URL 未配置")
        raise HTTPException(500, "FEISHU_WEBHOOK_URL 未配置")
    logger.info(f"发送富文本：title={req.title}, {len(req.content)} 行")
    # 转换为飞书 API 格式
    # 飞书 Webhook Bot 的 post 格式不支持 style 字段
    # 需要过滤掉 style，保留 tag/text/href/user_id
    content = []
    for line in req.content:
        row = []
        for el in line:
            item = {"tag": el.tag}
            if el.text is not None:
                item["text"] = el.text
            if el.href is not None:
                item["href"] = el.href
            if el.user_id is not None:
                item["user_id"] = el.user_id
            # 注意：不传 style 字段，Webhook Bot 不支持
            row.append(item)
        content.append(row)
    result = _send_rich(WEBHOOK_URL, req.title, content, WEBHOOK_SECRET)
    if result.get("code") == 0:
        logger.info("富文本发送成功")
        return SendResponse(ok=True, message="富文本消息已发送")
    logger.error(f"富文本发送失败：{result}")
    return SendResponse(ok=False, message=str(result))


@app.post("/test", response_model=SendResponse)
def test():
    if not WEBHOOK_URL:
        raise HTTPException(500, "FEISHU_WEBHOOK_URL 未配置")
    text = f"Agent Platform 连接测试\n时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    result = _send_text(WEBHOOK_URL, text, WEBHOOK_SECRET)
    if result.get("code") == 0:
        return SendResponse(ok=True, message="连接测试成功")
    return SendResponse(ok=False, message=str(result))


# ---------------------------------------------------------------------------
# 前端页面（独立 HTML，嵌入 iframe 使用）
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>飞书群消息推送</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f7f8fa; color: #1f2329; }
  .container { max-width: 640px; margin: 0 auto; padding: 24px 16px; }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 20px; }

  .card { background: #fff; border-radius: 10px; border: 1px solid #e5e6eb; padding: 20px; margin-bottom: 16px; }
  .card-title { font-size: 14px; font-weight: 500; color: #646a73; margin-bottom: 12px; }

  .toolbar { display: flex; gap: 4px; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #f0f1f5; flex-wrap: wrap; }
  .toolbar button {
    padding: 4px 10px; border: 1px solid #e5e6eb; border-radius: 6px; background: #fff;
    cursor: pointer; font-size: 13px; color: #1f2329; transition: all 0.15s;
  }
  .toolbar button:hover { background: #f0f1f5; }
  .toolbar button.active { background: #e8f0fe; border-color: #4e83fd; color: #4e83fd; }

  .editor {
    min-height: 160px; padding: 12px; border: 1px solid #e5e6eb; border-radius: 8px;
    font-size: 14px; line-height: 1.6; outline: none; background: #fff;
  }
  .editor:focus { border-color: #4e83fd; box-shadow: 0 0 0 2px rgba(78,131,253,0.15); }
  .editor:empty::before { content: attr(data-placeholder); color: #bbbfc4; }

  .title-input {
    width: 100%; padding: 8px 12px; border: 1px solid #e5e6eb; border-radius: 8px;
    font-size: 14px; margin-bottom: 8px; outline: none;
  }
  .title-input:focus { border-color: #4e83fd; }

  .actions { display: flex; gap: 8px; margin-top: 12px; }
  .btn {
    padding: 8px 20px; border-radius: 8px; border: none; font-size: 14px;
    cursor: pointer; font-weight: 500; transition: all 0.15s;
  }
  .btn-primary { background: #4e83fd; color: #fff; }
  .btn-primary:hover { background: #3b71ec; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary { background: #f0f1f5; color: #1f2329; }
  .btn-secondary:hover { background: #e5e6eb; }

  .status { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 13px; display: none; }
  .status.success { display: block; background: #e8f7e8; color: #1a7f1a; }
  .status.error { display: block; background: #fde8e8; color: #d83931; }

  .mode-tabs { display: flex; gap: 0; margin-bottom: 16px; }
  .mode-tab {
    padding: 8px 20px; border: 1px solid #e5e6eb; background: #fff; cursor: pointer;
    font-size: 13px; color: #646a73; transition: all 0.15s;
  }
  .mode-tab:first-child { border-radius: 8px 0 0 8px; }
  .mode-tab:last-child { border-radius: 0 8px 8px 0; }
  .mode-tab.active { background: #4e83fd; color: #fff; border-color: #4e83fd; }

  .history { font-size: 13px; color: #646a73; }
  .history-item { padding: 8px 0; border-bottom: 1px solid #f0f1f5; }
  .history-item:last-child { border: none; }
  .history-time { font-size: 12px; color: #bbbfc4; }
</style>
</head>
<body>
<div class="container">
  <h1>📨 飞书群消息推送</h1>

  <div class="mode-tabs">
    <div class="mode-tab active" onclick="switchMode('text')">纯文本</div>
    <div class="mode-tab" onclick="switchMode('rich')">富文本</div>
  </div>

  <!-- 纯文本模式 -->
  <div id="text-mode" class="card">
    <div class="card-title">消息内容</div>
    <textarea id="text-input" style="width:100%;min-height:160px;padding:12px;border:1px solid #e5e6eb;border-radius:8px;font-size:14px;line-height:1.6;outline:none;resize:vertical;font-family:inherit;" placeholder="输入要发送的文本消息..."></textarea>
    <div class="actions">
      <button class="btn btn-primary" onclick="sendText()" id="send-text-btn">发送</button>
      <button class="btn btn-secondary" onclick="testConnection()">连接测试</button>
    </div>
  </div>

  <!-- 富文本模式 -->
  <div id="rich-mode" class="card" style="display:none">
    <div class="card-title">标题（可选）</div>
    <input class="title-input" id="rich-title" placeholder="消息标题">
    <div class="card-title" style="margin-top:12px">内容</div>
    <div class="toolbar">
      <button onclick="insertLink()" title="插入链接">🔗 插入链接</button>
      <span style="font-size:12px;color:#bbbfc4;line-height:28px;margin-left:8px">支持文本 + 链接，换行会保留</span>
    </div>
    <div class="editor" id="rich-editor" contenteditable="true" data-placeholder="输入内容，支持多行文本和链接..."></div>
    <div class="actions">
      <button class="btn btn-primary" onclick="sendRich()" id="send-rich-btn">发送</button>
      <button class="btn btn-secondary" onclick="testConnection()">连接测试</button>
    </div>
  </div>

  <div class="status" id="status"></div>

  <div class="card" style="margin-top:8px">
    <div class="card-title">发送历史</div>
    <div class="history" id="history">
      <div style="color:#bbbfc4;text-align:center;padding:16px;">暂无记录</div>
    </div>
  </div>
</div>

<script>
const API = window.location.origin;
let mode = 'text';
let history = JSON.parse(localStorage.getItem('feishu_history') || '[]');
renderHistory();

function switchMode(m) {
  mode = m;
  document.querySelectorAll('.mode-tab').forEach((t, i) => {
    t.classList.toggle('active', (i === 0 && m === 'text') || (i === 1 && m === 'rich'));
  });
  document.getElementById('text-mode').style.display = m === 'text' ? 'block' : 'none';
  document.getElementById('rich-mode').style.display = m === 'rich' ? 'block' : 'none';
}

function showStatus(msg, ok) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status ' + (ok ? 'success' : 'error');
  setTimeout(() => { el.className = 'status'; }, 5000);
}

function addHistory(type, content, ok) {
  history.unshift({ type, content: content.substring(0, 100), ok, time: new Date().toLocaleString() });
  if (history.length > 20) history = history.slice(0, 20);
  localStorage.setItem('feishu_history', JSON.stringify(history));
  renderHistory();
}

function renderHistory() {
  const el = document.getElementById('history');
  if (history.length === 0) {
    el.innerHTML = '<div style="color:#bbbfc4;text-align:center;padding:16px;">暂无记录</div>';
    return;
  }
  el.innerHTML = history.map(h =>
    '<div class="history-item">' +
    '<span style="color:' + (h.ok ? '#1a7f1a' : '#d83931') + '">' + (h.ok ? '✓' : '✗') + '</span> ' +
    '<span>' + escapeHtml(h.content) + '</span> ' +
    '<span class="history-time">' + h.time + '</span>' +
    '</div>'
  ).join('');
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function sendText() {
  const text = document.getElementById('text-input').value.trim();
  if (!text) return;
  const btn = document.getElementById('send-text-btn');
  btn.disabled = true; btn.textContent = '发送中...';
  try {
    const res = await fetch(API + '/send', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    showStatus(data.message, data.ok);
    addHistory('text', text, data.ok);
    if (data.ok) document.getElementById('text-input').value = '';
  } catch (e) {
    showStatus('发送失败: ' + e.message, false);
  }
  btn.disabled = false; btn.textContent = '发送';
}

async function sendRich() {
  const title = document.getElementById('rich-title').value.trim();
  const editor = document.getElementById('rich-editor');
  const content = parseEditorContent(editor);
  if (content.length === 0 || (content.length === 1 && content[0].length === 0)) {
    showStatus('请输入内容', false); return;
  }
  const btn = document.getElementById('send-rich-btn');
  btn.disabled = true; btn.textContent = '发送中...';
  try {
    const res = await fetch(API + '/send_rich', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ title, content }),
    });
    const data = await res.json();
    showStatus(data.message, data.ok);
    addHistory('rich', title || editor.innerText.substring(0, 50), data.ok);
    if (data.ok) { editor.innerHTML = ''; document.getElementById('rich-title').value = ''; }
  } catch (e) {
    showStatus('发送失败: ' + e.message, false);
  }
  btn.disabled = false; btn.textContent = '发送';
}

function parseEditorContent(editor) {
  // 将 contenteditable 的 HTML 转为飞书 post 格式
  const lines = [];
  const childNodes = editor.childNodes.length ? editor.childNodes : [editor];

  function processNode(node) {
    const line = [];
    walkNode(node, line, []);
    if (line.length > 0) lines.push(line);
  }

  function walkNode(node, line, styles) {
    if (node.nodeType === 3) { // text
      const text = node.textContent;
      if (text) {
        const el = { tag: 'text', text };
        if (styles.length > 0) el.style = [...styles];
        line.push(el);
      }
      return;
    }
    if (node.nodeType !== 1) return;
    const tag = node.tagName.toLowerCase();
    const newStyles = [...styles];
    if (tag === 'b' || tag === 'strong') newStyles.push('bold');
    if (tag === 'i' || tag === 'em') newStyles.push('italic');
    if (tag === 'u') newStyles.push('underline');
    if (tag === 's' || tag === 'strike' || tag === 'del') newStyles.push('lineThrough');
    if (tag === 'a') {
      line.push({ tag: 'a', text: node.textContent, href: node.href || '' });
      return;
    }
    if (tag === 'br') {
      lines.push([...line]);
      line.length = 0;
      return;
    }
    if (tag === 'div' || tag === 'p') {
      if (line.length > 0) { lines.push([...line]); line.length = 0; }
      for (const child of node.childNodes) walkNode(child, line, newStyles);
      if (line.length > 0) { lines.push([...line]); line.length = 0; }
      return;
    }
    for (const child of node.childNodes) walkNode(child, line, newStyles);
  }

  const tempLine = [];
  for (const child of editor.childNodes) {
    walkNode(child, tempLine, []);
  }
  if (tempLine.length > 0) lines.push(tempLine);
  return lines.length > 0 ? lines : [[{ tag: 'text', text: editor.innerText || '' }]];
}

function formatText(cmd) {
  document.execCommand(cmd, false, null);
  document.getElementById('rich-editor').focus();
}

function insertLink() {
  const url = prompt('输入链接地址:', 'https://');
  if (!url) return;
  const text = prompt('链接文字:', url);
  document.execCommand('insertHTML', false, '<a href="' + url + '">' + (text || url) + '</a>');
}

async function testConnection() {
  try {
    const res = await fetch(API + '/test', { method: 'POST' });
    const data = await res.json();
    showStatus(data.message, data.ok);
    addHistory('test', '连接测试', data.ok);
  } catch (e) {
    showStatus('测试失败: ' + e.message, false);
  }
}

// 快捷键
document.getElementById('text-input').addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendText();
});
document.getElementById('rich-editor').addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendRich();
});
</script>
</body>
</html>"""
