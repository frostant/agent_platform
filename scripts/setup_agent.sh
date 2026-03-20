#!/bin/bash
# 为指定 Agent 创建独立 venv 并安装依赖
# 用法: ./scripts/setup_agent.sh <agent_id>
#       ./scripts/setup_agent.sh --all     # 为所有 Agent 创建

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$PROJECT_DIR/agents"

setup_one() {
    local agent_dir="$1"
    local agent_id="$(basename "$agent_dir")"
    local req_file="$agent_dir/requirements.txt"

    if [ ! -f "$agent_dir/agent.json" ]; then
        echo "跳过 $agent_id（无 agent.json）"
        return
    fi

    echo "=== 设置 $agent_id ==="

    # 创建 venv
    if [ ! -d "$agent_dir/venv" ]; then
        echo "  创建 venv..."
        python3 -m venv "$agent_dir/venv"
    else
        echo "  venv 已存在，跳过创建"
    fi

    # 安装依赖
    if [ -f "$req_file" ]; then
        echo "  安装依赖..."
        "$agent_dir/venv/bin/pip" install -q -r "$req_file"
    fi

    # 创建 data 目录
    mkdir -p "$agent_dir/data"

    echo "  完成"
}

if [ "$1" = "--all" ]; then
    for agent_dir in "$AGENTS_DIR"/*/; do
        setup_one "$agent_dir"
    done
elif [ -n "$1" ]; then
    agent_dir="$AGENTS_DIR/$1"
    if [ ! -d "$agent_dir" ]; then
        echo "Agent 目录不存在: $agent_dir"
        exit 1
    fi
    setup_one "$agent_dir"
else
    echo "用法:"
    echo "  $0 <agent_id>    为指定 Agent 创建 venv"
    echo "  $0 --all         为所有 Agent 创建 venv"
    exit 1
fi
