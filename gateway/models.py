"""数据模型定义"""

from __future__ import annotations
from typing import Optional, List
from pydantic import BaseModel
from enum import Enum


class AgentType(str, Enum):
    fastapi = "fastapi"
    streamlit = "streamlit"
    static = "static"


class AgentAccess(str, Enum):
    public = "public"
    root_only = "root_only"


class AgentConfig(BaseModel):
    """Agent 注册配置"""
    id: str
    name: str
    description: str
    icon: str = "box"
    type: AgentType
    port: Optional[int] = None
    entry: str = "app.py"
    access: AgentAccess = AgentAccess.public
    autostart: bool = False
    tags: List[str] = []
    dir: str = ""  # Agent 目录绝对路径（由 registry 填充）


class AgentStatus(str, Enum):
    running = "running"
    stopped = "stopped"
    error = "error"


class AgentInfo(BaseModel):
    """返回给前端的 Agent 信息"""
    id: str
    name: str
    description: str
    icon: str
    type: AgentType
    port: Optional[int]
    access: AgentAccess
    status: AgentStatus
    tags: List[str] = []


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    role: str = "root"
