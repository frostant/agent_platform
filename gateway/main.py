"""Agent 统一平台 — FastAPI 网关入口"""

import time
import logging
import urllib.request
import urllib.error
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import agent_registry, agent_manager
from .auth import get_role, require_root, verify_password, create_token
from .models import AgentInfo, AgentStatus, LoginRequest, LoginResponse

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "gateway.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("gateway")

PROJECT_DIR = Path(__file__).parent.parent
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 50)
    logger.info("网关启动")

    agent_registry.load()
    agents = agent_registry.get_all()
    logger.info(f"Agent 扫描完成: 发现 {len(agents)} 个 Agent")
    for a in agents:
        logger.info(f"  - {a.id} (type={a.type}, port={a.port}, autostart={a.autostart})")

    agent_manager.auto_start()
    status = agent_manager.status_all()
    for aid, st in status.items():
        logger.info(f"  Agent {aid}: {st}")

    logger.info("网关就绪，等待请求")
    yield

    logger.info("网关关闭中，停止所有 Agent...")
    agent_manager.stop_all()
    logger.info("网关已关闭")
    logger.info("=" * 50)


app = FastAPI(title="Agent Platform Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000)
    path = request.url.path
    if path in ("/api/health", "/api/agents"):
        logger.debug(f"{request.method} {path} → {response.status_code} ({duration}ms)")
    else:
        logger.info(f"{request.method} {path} → {response.status_code} ({duration}ms)")
    return response


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------

@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    if not verify_password(req.password):
        logger.warning("登录失败: 密码错误")
        raise HTTPException(401, "密码错误")
    logger.info("root 用户登录成功")
    return LoginResponse(token=create_token("root"))


@app.get("/api/auth/me")
def auth_me(role: str = Depends(get_role)):
    return {"role": role}


# ---------------------------------------------------------------------------
# Agent 列表
# ---------------------------------------------------------------------------

@app.get("/api/agents")
def list_agents(role: str = Depends(get_role)):
    agents = []
    for config in agent_registry.get_all():
        if config.access == "root_only" and role != "root":
            continue
        agents.append(AgentInfo(
            id=config.id,
            name=config.name,
            description=config.description,
            icon=config.icon,
            type=config.type,
            port=config.port,
            access=config.access,
            status=agent_manager.status(config.id),
            tags=config.tags,
        ))
    return agents


@app.get("/api/agents/{agent_id}", response_model=AgentInfo)
def get_agent(agent_id: str, role: str = Depends(get_role)):
    config = agent_registry.get(agent_id)
    if not config:
        raise HTTPException(404, "Agent 不存在")
    if config.access == "root_only" and role != "root":
        raise HTTPException(403, "无权限")
    return AgentInfo(
        id=config.id,
        name=config.name,
        description=config.description,
        icon=config.icon,
        type=config.type,
        port=config.port,
        access=config.access,
        status=agent_manager.status(config.id),
        tags=config.tags,
    )


# ---------------------------------------------------------------------------
# Agent 管理（仅 root）
# ---------------------------------------------------------------------------

@app.post("/api/agents/{agent_id}/start")
def start_agent(agent_id: str, _=Depends(require_root)):
    logger.info(f"请求启动 Agent: {agent_id}")
    if agent_manager.start(agent_id):
        return {"ok": True, "status": "running"}
    logger.error(f"启动 Agent 失败: {agent_id}")
    raise HTTPException(500, "启动失败")


@app.post("/api/agents/{agent_id}/stop")
def stop_agent(agent_id: str, _=Depends(require_root)):
    logger.info(f"请求停止 Agent: {agent_id}")
    if agent_manager.stop(agent_id):
        return {"ok": True, "status": "stopped"}
    raise HTTPException(500, "停止失败")


@app.post("/api/agents/{agent_id}/restart")
def restart_agent(agent_id: str, _=Depends(require_root)):
    logger.info(f"请求重启 Agent: {agent_id}")
    if agent_manager.restart(agent_id):
        return {"ok": True, "status": "running"}
    logger.error(f"重启 Agent 失败: {agent_id}")
    raise HTTPException(500, "重启失败")


@app.post("/api/agents/reload")
def reload_agents(_=Depends(require_root)):
    agent_registry.reload()
    count = len(agent_registry.get_all())
    logger.info(f"重新扫描 Agent: 发现 {count} 个")
    return {"ok": True, "count": count}


@app.get("/api/health")
def health():
    return {"status": "ok", "agents": agent_manager.status_all()}


# ---------------------------------------------------------------------------
# Agent 反向代理：/agent/<id>/{path} → localhost:{port}/{path}
# 让所有服务通过单端口暴露（Render 部署需要）
# ---------------------------------------------------------------------------

@app.api_route("/agent/{agent_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_agent(agent_id: str, path: str, request: Request):
    config = agent_registry.get(agent_id)
    if not config or not config.port:
        raise HTTPException(404, "Agent 不存在")

    target_url = f"http://localhost:{config.port}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    body = await request.body() if request.method in ("POST", "PUT") else None

    try:
        req = urllib.request.Request(
            target_url,
            data=body,
            method=request.method,
        )
        # 转发 headers
        for key in ("content-type", "authorization", "accept"):
            val = request.headers.get(key)
            if val:
                req.add_header(key, val)

        with urllib.request.urlopen(req, timeout=300) as resp:
            resp_body = resp.read()
            resp_headers = dict(resp.getheaders())
            content_type = resp_headers.get("Content-Type", "application/octet-stream")
            return Response(
                content=resp_body,
                status_code=resp.status,
                media_type=content_type,
            )
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code)
    except Exception as e:
        logger.error(f"代理 {agent_id}/{path} 失败: {e}")
        raise HTTPException(502, f"Agent 不可达: {e}")


# ---------------------------------------------------------------------------
# 前端静态文件（生产模式：serve frontend/dist）
# ---------------------------------------------------------------------------

if FRONTEND_DIST.exists():
    # SPA fallback：非 API/agent 路径都返回 index.html
    from fastapi.responses import FileResponse

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        file_path = FRONTEND_DIST / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
