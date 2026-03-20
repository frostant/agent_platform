#!/bin/bash
# 启动 Agent 平台（网关会自动拉起所有 autostart Agent）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "启动 Agent Platform Gateway..."
python -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 &
GATEWAY_PID=$!
echo "Gateway PID: $GATEWAY_PID"
echo "$GATEWAY_PID" > "$PROJECT_DIR/config/gateway.pid"

echo "Gateway 已启动: http://localhost:8000"
echo "API 文档: http://localhost:8000/docs"
