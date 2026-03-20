#!/bin/bash
# 检查每个 Agent 的代码是否自包含：无外部路径依赖、无 sys.path hack
# 用法: ./scripts/check_isolation.sh [agent_id]
#       ./scripts/check_isolation.sh          # 检查所有 Agent

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$PROJECT_DIR/agents"
RESULT=0

check_agent() {
    local agent_dir="$1"
    local agent_id="$(basename "$agent_dir")"

    [ -f "$agent_dir/agent.json" ] || return

    echo "--- $agent_id ---"
    local fail=0

    # 1. 检查 sys.path hack（不应依赖外部路径）
    if grep -rn "sys\.path\.\(insert\|append\)" "$agent_dir" --include="*.py" 2>/dev/null | grep -v venv | grep -v __pycache__; then
        echo "  [FAIL] 发现 sys.path hack（应使用相对 import）"
        fail=1
    else
        echo "  [PASS] 无 sys.path hack"
    fi

    # 2. 检查绝对路径引用
    if grep -rn "/Users/\|/home/\|/tmp/" "$agent_dir" --include="*.py" 2>/dev/null | grep -v venv | grep -v __pycache__ | grep -v "\.log"; then
        echo "  [FAIL] 发现硬编码绝对路径"
        fail=1
    else
        echo "  [PASS] 无硬编码绝对路径"
    fi

    # 3. 检查是否引用了 Agent 沙箱外的模块
    local external_imports=$(grep -rn "^from \|^import " "$agent_dir" --include="*.py" 2>/dev/null \
        | grep -v venv | grep -v __pycache__ \
        | grep -v "from \." \
        | grep -v "import \(json\|sys\|os\|time\|pathlib\|datetime\|argparse\|logging\|traceback\|hashlib\|base64\|hmac\|urllib\|re\|abc\|collections\|functools\|typing\|enum\|contextlib\)" \
        | grep -v "from \(pathlib\|datetime\|typing\|enum\|contextlib\|collections\|abc\)" \
        | grep -v "from \(fastapi\|pydantic\|uvicorn\|requests\|dotenv\|starlette\)" \
        | grep -v "import \(fastapi\|pydantic\|uvicorn\|requests\)" \
        | grep -v "from digest\|from libra_sdk\|from config" \
        || true)

    if [ -n "$external_imports" ]; then
        echo "  [WARN] 可能的外部 import（请确认在 requirements.txt 中声明）:"
        echo "$external_imports" | sed 's/^/    /'
    else
        echo "  [PASS] 所有 import 为标准库/声明依赖/相对导入"
    fi

    # 4. 检查 requirements.txt 是否存在
    if [ -f "$agent_dir/requirements.txt" ]; then
        echo "  [PASS] requirements.txt 存在"
    else
        echo "  [FAIL] requirements.txt 缺失"
        fail=1
    fi

    # 5. 检查是否有 test_e2e.py
    if [ -f "$agent_dir/test_e2e.py" ]; then
        echo "  [PASS] test_e2e.py 存在"
    else
        echo "  [FAIL] test_e2e.py 缺失"
        fail=1
    fi

    if [ $fail -eq 1 ]; then
        RESULT=1
    fi
}

echo "============================================"
echo "  Agent 沙箱隔离检查"
echo "============================================"
echo ""

if [ -n "$1" ]; then
    check_agent "$AGENTS_DIR/$1"
else
    for agent_dir in "$AGENTS_DIR"/*/; do
        check_agent "$agent_dir"
        echo ""
    done
fi

echo "============================================"
if [ $RESULT -eq 0 ]; then
    echo "  全部检查通过"
else
    echo "  存在隔离问题，请修复"
fi
echo "============================================"
exit $RESULT
