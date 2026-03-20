"""Agent 统一平台 — FastAPI 网关入口"""

import time
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

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


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 50)
    logger.info("网关启动")

    # 加载注册表
    agent_registry.load()
    agents = agent_registry.get_all()
    logger.info(f"Agent 扫描完成: 发现 {len(agents)} 个 Agent")
    for a in agents:
        logger.info(f"  - {a.id} (type={a.type}, port={a.port}, autostart={a.autostart})")

    # 自动启动
    agent_manager.auto_start()
    status = agent_manager.status_all()
    for aid, st in status.items():
        logger.info(f"  Agent {aid}: {st}")

    logger.info("网关就绪，等待请求")
    yield

    # 关闭
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
    # 健康检查和 Agent 列表轮询太频繁，降级为 DEBUG
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
