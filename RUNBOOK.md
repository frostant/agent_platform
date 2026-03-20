# Runbook

## Setup
```bash
pip install fastapi uvicorn                # 网关依赖
cd frontend && npm install                 # 前端依赖
./scripts/setup_agent.sh --all             # 所有 Agent 创建 venv
```

## 启动
```bash
# 网关（自动拉起所有 autostart Agent）
python3 -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000

# 前端开发
cd frontend && npm run dev    # → http://localhost:5173
```

## 测试
```bash
./scripts/check_all.sh                     # 全量自测（5 步）
./scripts/check_isolation.sh               # 仅隔离检查
python3 tests/test_gateway.py              # 仅后端 API
cd frontend && npm run build               # 仅前端构建

# 单 Agent 测试
python3 agents/feishu_notify/test_e2e.py --port 8501 --lite
python3 agents/daily_digest/test_e2e.py --port 8502 --lite

# 全量模式（发布前）
E2E_MODE=--live ./scripts/check_all.sh
```

## Agent 管理
```bash
./scripts/setup_agent.sh <id>              # 新 Agent 创建 venv
./scripts/install_launchd.sh               # 安装开机自启
./scripts/uninstall_launchd.sh             # 卸载
launchctl list | grep agent-platform       # 查看状态
```

## 环境变量
- `config/secrets.json` — root_password（默认 admin）、jwt_secret
- `agents/feishu_notify/.env` — FEISHU_WEBHOOK_URL
- `agents/daily_digest/digest/cookies.json` — Libra Cookie

## 日志
| 文件 | 内容 |
|------|------|
| `logs/gateway.log` | 网关启动/Agent 管理/请求 |
| `logs/launchd_*.log` | launchd 捕获的输出 |
| `agents/<id>/data/<id>.log` | Agent 业务日志 |

## Debug Tips
| 问题 | 排查 |
|------|------|
| Agent "未运行" | 网关会自动端口探活，如仍误报则通过 API restart |
| 飞书 19002 | payload 含 style 字段，需过滤 |
| `import type` 错误 | 前端 import 类型必须用 `import type { X }` |
| digest 全 null | 检查日期范围和 cookies 有效性 |
| venv 缺依赖 | `./scripts/setup_agent.sh <id>` |
