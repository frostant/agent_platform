#!/bin/bash
# 前端自动检查：类型检查 + 构建检查
# 用法: ./scripts/check_frontend.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$(dirname "$SCRIPT_DIR")/frontend"

cd "$FRONTEND_DIR"

echo "=== [1/2] TypeScript 类型检查 ==="
npx tsc -b --noEmit 2>&1
if [ $? -eq 0 ]; then
    echo "  通过"
else
    echo "  失败"
    exit 1
fi

echo ""
echo "=== [2/2] Vite 构建检查 ==="
npm run build 2>&1
if [ $? -eq 0 ]; then
    echo ""
    echo "=== 全部检查通过 ==="
else
    echo ""
    echo "=== 构建失败 ==="
    exit 1
fi
