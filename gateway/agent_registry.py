"""Agent 注册表：扫描 agents/*/agent.json 自动发现"""

from __future__ import annotations
import json
from typing import Dict, List, Optional
from pathlib import Path
from .models import AgentConfig

_agents: Dict[str, AgentConfig] = {}
_agents_dir = Path(__file__).parent.parent / "agents"


def load():
    """扫描 agents/ 下所有子目录的 agent.json，自动注册"""
    global _agents
    _agents = {}
    if not _agents_dir.exists():
        return
    for agent_json in _agents_dir.glob("*/agent.json"):
        try:
            data = json.loads(agent_json.read_text())
            config = AgentConfig(**data)
            # 记录 Agent 所在目录的绝对路径
            config.dir = str(agent_json.parent.resolve())
            _agents[config.id] = config
        except Exception as e:
            print(f"[registry] 跳过 {agent_json}: {e}")


def get_all() -> List[AgentConfig]:
    return list(_agents.values())


def get(agent_id: str) -> Optional[AgentConfig]:
    return _agents.get(agent_id)


def reload():
    """热加载：重新扫描"""
    load()
