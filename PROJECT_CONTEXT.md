# Project Context

## Overview
统一 Agent 管理平台，聚合多个独立 Agent 到一个 Web 入口。Gateway + 子进程隔离架构。

## Architecture
```
浏览器 → Nginx → ┬─ /        React 前端
                  ├─ /api/*   FastAPI 网关（:8000）
                  └─ /agent/* Agent 子进程（各自端口）
```

| 模块 | 路径 | 职责 |
|------|------|------|
| 网关 | `gateway/` | 入口、JWT 认证、Agent 发现、进程管理 |
| 前端 | `frontend/` | React SPA，侧边栏+工作台，iframe 嵌入 Agent |
| Agent | `agents/<id>/` | 自包含沙箱：代码、venv、配置、数据、自测 |
| 脚本 | `scripts/` | 启停、venv 初始化、自测、隔离检查、launchd 安装 |

## Key Decisions

| 决策 | 原因 |
|------|------|
| 分布式 agent.json | 添加 Agent 零配置 |
| iframe 嵌入 | 零改造成本 |
| venv 隔离 | 轻量够用，后续可升级 Docker |
| 手撸 JWT | 零依赖 |
| 端口探活兜底 | 解决手动重启后状态误报 |
| Agent 代码自包含 | 不依赖外部路径，可独立部署 |

## Known Issues

| 问题 | 状态 |
|------|------|
| Python 3.9 不支持 `int \| None` | 已解决，用 Optional |
| Vite 类型导出丢失 | 已解决，用 import type |
| Agent 状态误报 | 已解决，端口探活兜底 |
| 飞书 post 不支持 style | 已解决，过滤掉 |
| 全量 digest 耗时长 | 已知，串行遍历 |
