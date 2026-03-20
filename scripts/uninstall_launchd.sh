#!/bin/bash
# 卸载 launchd 服务
# 用法: ./scripts/uninstall_launchd.sh

PLIST_NAME="com.agent-platform.gateway"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "=== Agent Platform: 卸载 launchd 服务 ==="

if [ -f "$PLIST_DEST" ]; then
    launchctl unload "$PLIST_DEST" 2>/dev/null
    rm "$PLIST_DEST"
    echo "已卸载并删除: $PLIST_DEST"
else
    echo "服务未安装 ($PLIST_DEST 不存在)"
fi

# 确认端口已释放
sleep 2
if lsof -ti:8000 > /dev/null 2>&1; then
    echo "警告: 端口 8000 仍被占用"
    echo "  PID: $(lsof -ti:8000)"
else
    echo "端口 8000 已释放"
fi
