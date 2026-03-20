#!/bin/bash
# 全量自测：前端构建 + 后端 API + Agent 配置 + Agent 端到端测试
# 用法: ./scripts/check_all.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RESULT=0

cd "$PROJECT_DIR"

echo "============================================"
echo "  Agent Platform 全量自测"
echo "============================================"

# --- 1. 前端构建 ---
echo ""
echo "[1/4] 前端构建检查"
echo "--------------------------------------------"
cd "$PROJECT_DIR/frontend"
if npm run build 2>&1; then
    echo "  [PASS] 前端构建成功"
else
    echo "  [FAIL] 前端构建失败"
    RESULT=1
fi

# --- 2. 后端 API 测试 ---
echo ""
echo "[2/4] 后端 API 测试"
echo "--------------------------------------------"
cd "$PROJECT_DIR"

STARTED_GATEWAY=""
if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "  网关已运行，直接测试"
else
    echo "  网关未运行，启动中..."
    python3 -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 &
    STARTED_GATEWAY=$!
    sleep 4
    echo "  网关已启动 (PID=$STARTED_GATEWAY)"
fi

if python3 tests/test_gateway.py 2>&1; then
    echo "  [PASS] 后端测试通过"
else
    echo "  [FAIL] 后端测试失败"
    RESULT=1
fi

# --- 3. Agent 配置校验 ---
echo ""
echo "[3/4] Agent 配置校验"
echo "--------------------------------------------"
cd "$PROJECT_DIR"
for agent_json in agents/*/agent.json; do
    agent_dir=$(dirname "$agent_json")
    agent_id=$(basename "$agent_dir")

    if python3 -c "import json; json.load(open('$agent_json'))" 2>/dev/null; then
        echo "  [PASS] $agent_id/agent.json 格式正确"
    else
        echo "  [FAIL] $agent_id/agent.json 格式错误"
        RESULT=1
    fi

    entry=$(python3 -c "import json; print(json.load(open('$agent_json')).get('entry','app.py'))" 2>/dev/null)
    entry_file="$agent_dir/${entry%.py}.py"
    if [ -f "$agent_dir/$entry" ] || [ -f "$entry_file" ]; then
        echo "  [PASS] $agent_id 入口文件存在"
    else
        echo "  [FAIL] $agent_id 入口文件缺失: $entry"
        RESULT=1
    fi
done

# --- 4. Agent 沙箱隔离检查 ---
echo ""
echo "[4/5] Agent 沙箱隔离检查"
echo "--------------------------------------------"
if bash "$PROJECT_DIR/scripts/check_isolation.sh" 2>&1; then
    echo "  [PASS] 隔离检查通过"
else
    echo "  [FAIL] 隔离检查失败"
    RESULT=1
fi

# --- 5. Agent 端到端测试 ---
echo ""
echo "[5/5] Agent 端到端测试"
echo "--------------------------------------------"
cd "$PROJECT_DIR"
for test_file in agents/*/test_e2e.py; do
    [ -f "$test_file" ] || continue
    agent_dir=$(dirname "$test_file")
    agent_id=$(basename "$agent_dir")
    port=$(python3 -c "import json; print(json.load(open('$agent_dir/agent.json')).get('port',''))" 2>/dev/null)

    echo "  --- $agent_id (port=$port) ---"

    if [ -n "$port" ] && curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
        # 默认用 --lite 模式（快速），加 E2E_MODE=live 可切换全量
        E2E_FLAG="${E2E_MODE:---lite}"
        if python3 "$test_file" --port "$port" $E2E_FLAG 2>&1; then
            echo "  [PASS] $agent_id 端到端测试通过"
        else
            echo "  [FAIL] $agent_id 端到端测试失败"
            RESULT=1
        fi
    else
        echo "  [SKIP] $agent_id 未运行 (port=$port)"
    fi
done

# 如果是我们启动的网关，关掉
if [ -n "$STARTED_GATEWAY" ]; then
    kill $STARTED_GATEWAY 2>/dev/null
fi

# --- 汇总 ---
echo ""
echo "============================================"
if [ $RESULT -eq 0 ]; then
    echo "  全部检查通过"
else
    echo "  存在失败项，请修复后重试"
fi
echo "============================================"

exit $RESULT
