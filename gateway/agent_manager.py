"""Agent 进程管理器：启动/停止/重启/状态查询，进程隔离"""

from __future__ import annotations
import subprocess
import json
import logging
from typing import Dict, List
from pathlib import Path
from .models import AgentConfig, AgentType, AgentStatus
from . import agent_registry

logger = logging.getLogger("gateway.manager")

_processes: Dict[str, subprocess.Popen] = {}
_state_path = Path(__file__).parent.parent / "config" / "state.json"


def _get_python(agent_dir: str) -> str:
    venv_python = Path(agent_dir) / "venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return "python3"


def _build_cmd(config: AgentConfig) -> List[str]:
    python = _get_python(config.dir)

    if config.type == AgentType.fastapi:
        module = config.entry.replace(".py", "").replace("/", ".")
        return [
            python, "-m", "uvicorn",
            f"{module}:app",
            "--host", "0.0.0.0",
            "--port", str(config.port),
        ]
    elif config.type == AgentType.streamlit:
        venv_streamlit = Path(config.dir) / "venv" / "bin" / "streamlit"
        st = str(venv_streamlit) if venv_streamlit.exists() else "streamlit"
        return [
            st, "run", config.entry,
            "--server.port", str(config.port),
            "--server.headless", "true",
            "--server.address", "0.0.0.0",
        ]
    else:
        return []


def start(agent_id: str) -> bool:
    if agent_id in _processes and _processes[agent_id].poll() is None:
        logger.debug(f"{agent_id} 已在运行 (PID={_processes[agent_id].pid})")
        return True

    config = agent_registry.get(agent_id)
    if not config:
        logger.error(f"启动失败: Agent {agent_id} 未注册")
        return False
    if config.type == AgentType.static:
        logger.debug(f"{agent_id} 是 static 类型，无需启动")
        return False

    # 端口已被占用（可能是手动启动的）→ 视为已运行
    if _port_alive(config.port):
        logger.info(f"{agent_id} 端口 {config.port} 已有服务运行，跳过启动")
        # 清理旧的失效进程记录
        if agent_id in _processes:
            del _processes[agent_id]
            _save_state()
        return True

    cmd = _build_cmd(config)
    if not cmd:
        logger.error(f"启动失败: 无法构建 {agent_id} 的启动命令")
        return False

    agent_dir = config.dir
    python_path = cmd[0]
    logger.info(f"启动 {agent_id}: cmd={' '.join(cmd)}")
    logger.info(f"  cwd={agent_dir}, python={python_path}")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=agent_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _processes[agent_id] = proc
        _save_state()
        logger.info(f"已启动 {agent_id} (PID={proc.pid}, port={config.port})")
        return True
    except FileNotFoundError as e:
        logger.error(f"启动 {agent_id} 失败: 可执行文件不存在 — {e}")
        return False
    except PermissionError as e:
        logger.error(f"启动 {agent_id} 失败: 权限不足 — {e}")
        return False
    except Exception as e:
        logger.error(f"启动 {agent_id} 失败: {type(e).__name__} — {e}")
        return False


def stop(agent_id: str) -> bool:
    proc = _processes.get(agent_id)
    if not proc:
        return True
    if proc.poll() is not None:
        exit_code = proc.returncode
        logger.info(f"{agent_id} 已退出 (exit_code={exit_code})")
        del _processes[agent_id]
        _save_state()
        return True
    try:
        logger.info(f"停止 {agent_id} (PID={proc.pid})...")
        proc.terminate()
        proc.wait(timeout=5)
        logger.info(f"已停止 {agent_id}")
    except subprocess.TimeoutExpired:
        logger.warning(f"{agent_id} terminate 超时，强制 kill")
        proc.kill()
        proc.wait()
    del _processes[agent_id]
    _save_state()
    return True


def restart(agent_id: str) -> bool:
    stop(agent_id)
    return start(agent_id)


def _port_alive(port):
    """检查端口是否有服务在监听"""
    if not port:
        return False
    import urllib.request
    try:
        urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
        return True
    except Exception:
        return False


def status(agent_id: str) -> AgentStatus:
    config = agent_registry.get(agent_id)
    port = config.port if config else None

    proc = _processes.get(agent_id)

    # 有子进程且在运行
    if proc and proc.poll() is None:
        return AgentStatus.running

    # 子进程不存在或已退出 → ping 端口兜底
    # 场景：Agent 被外部重启、手动启动、或网关重启后 PID 丢失
    if _port_alive(port):
        if proc:
            # 清理已失效的旧进程记录
            del _processes[agent_id]
            _save_state()
        logger.debug(f"{agent_id} 子进程丢失但端口 {port} 可达，判定为 running")
        return AgentStatus.running

    if proc:
        exit_code = proc.poll()
        logger.warning(f"{agent_id} 进程已退出 (exit_code={exit_code})，端口 {port} 不可达")
        return AgentStatus.error

    return AgentStatus.stopped


def status_all() -> Dict[str, AgentStatus]:
    result = {}
    for config in agent_registry.get_all():
        result[config.id] = status(config.id)
    return result


def auto_start():
    for config in agent_registry.get_all():
        if config.autostart:
            start(config.id)


def stop_all():
    for agent_id in list(_processes.keys()):
        stop(agent_id)


def _save_state():
    state = {}
    for agent_id, proc in _processes.items():
        state[agent_id] = {
            "desired": "running" if proc.poll() is None else "stopped",
            "pid": proc.pid,
        }
    _state_path.parent.mkdir(parents=True, exist_ok=True)
    _state_path.write_text(json.dumps(state, indent=2))
