#!/bin/bash
# 安装 launchd 服务：开机自动启动 Agent Platform 网关
# 用法: ./scripts/install_launchd.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_TEMPLATE="$SCRIPT_DIR/com.agent-platform.gateway.plist"
PLIST_NAME="com.agent-platform.gateway"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
PYTHON3_PATH=$(which python3)

echo "=== Agent Platform: 安装 launchd 服务 ==="
echo "  项目目录: $PROJECT_DIR"
echo "  Python3:  $PYTHON3_PATH"
echo "  Plist:    $PLIST_DEST"
echo ""

# 确保日志目录存在
mkdir -p "$PROJECT_DIR/logs"

# 如果已安装，先卸载
if launchctl list | grep -q "$PLIST_NAME" 2>/dev/null; then
    echo "检测到已有服务，先卸载..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# 从模板生成 plist（替换占位符）
sed \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__PYTHON3_PATH__|$PYTHON3_PATH|g" \
    "$PLIST_TEMPLATE" > "$PLIST_DEST"

echo "Plist 已写入: $PLIST_DEST"

# 加载服务
launchctl load "$PLIST_DEST"

echo ""
echo "=== 安装完成 ==="
echo ""
echo "  服务已启动，网关运行在 http://localhost:8000"
echo "  日志文件:"
echo "    - 网关日志: $PROJECT_DIR/logs/gateway.log"
echo "    - launchd stdout: $PROJECT_DIR/logs/launchd_stdout.log"
echo "    - launchd stderr: $PROJECT_DIR/logs/launchd_stderr.log"
echo ""
echo "  管理命令:"
echo "    查看状态:  launchctl list | grep agent-platform"
echo "    停止服务:  launchctl unload $PLIST_DEST"
echo "    启动服务:  launchctl load $PLIST_DEST"
echo "    卸载:      ./scripts/uninstall_launchd.sh"
echo ""

# 验证
sleep 3
if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "验证: 网关已正常运行"
else
    echo "警告: 网关尚未响应，请检查日志:"
    echo "  tail -20 $PROJECT_DIR/logs/launchd_stderr.log"
fi
