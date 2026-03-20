#!/bin/bash
# 停止 Agent 平台

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PID_FILE="$PROJECT_DIR/config/gateway.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "停止 Gateway (PID: $PID)..."
        kill "$PID"
        rm "$PID_FILE"
        echo "已停止"
    else
        echo "Gateway 进程不存在"
        rm "$PID_FILE"
    fi
else
    echo "未找到 PID 文件，尝试通过端口查找..."
    lsof -ti:8000 | xargs kill 2>/dev/null && echo "已停止" || echo "未找到运行中的 Gateway"
fi
