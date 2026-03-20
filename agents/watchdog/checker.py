"""健康检查核心逻辑

对每个 Agent 执行：
1. /health 存活检查
2. test_e2e.py 功能验证（lite 或 live 模式）
结果持久化到 data/health_log.json
"""

import json
import subprocess
import logging
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("watchdog.checker")

GATEWAY_URL = "http://localhost:8000"
PROJECT_DIR = Path(__file__).parent.parent.parent  # agents/watchdog -> project root
HEALTH_LOG = Path(__file__).parent / "data" / "health_log.json"


def _http_get(url, timeout=5):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except Exception as e:
        return 0, {"error": str(e)}


def _http_post(url, data=None, token=None, timeout=10):
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def check_agent_health(agent_id: str, port: int) -> dict:
    """检查单个 Agent 的 /health"""
    code, data = _http_get(f"http://localhost:{port}/health")
    return {
        "check": "health",
        "ok": code == 200 and data.get("status") == "ok",
        "status_code": code,
        "detail": data,
    }


def check_agent_e2e(agent_id: str, port: int, mode: str = "lite") -> dict:
    """运行 Agent 的 test_e2e.py"""
    test_file = PROJECT_DIR / "agents" / agent_id / "test_e2e.py"
    if not test_file.exists():
        return {"check": "e2e", "ok": False, "detail": "test_e2e.py 不存在"}

    cmd = ["python3", str(test_file), "--port", str(port)]
    if mode == "lite":
        cmd.append("--lite")
    elif mode == "live":
        cmd.append("--live")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        # 从输出中提取通过/失败数
        output = result.stdout + result.stderr
        passed = 0
        failed = 0
        for line in output.split("\n"):
            if "通过" in line and "失败" in line:
                import re
                m = re.search(r"(\d+) 通过.*?(\d+) 失败", line)
                if m:
                    passed = int(m.group(1))
                    failed = int(m.group(2))

        return {
            "check": "e2e",
            "mode": mode,
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "passed": passed,
            "failed": failed,
            "output_tail": output[-500:] if output else "",
        }
    except subprocess.TimeoutExpired:
        return {"check": "e2e", "mode": mode, "ok": False, "detail": "超时(600s)"}
    except Exception as e:
        return {"check": "e2e", "mode": mode, "ok": False, "detail": str(e)}


def get_agents_from_gateway(token: Optional[str] = None) -> list:
    """从网关获取所有 Agent 列表（需 root token 看全部）"""
    headers_str = ""
    url = f"{GATEWAY_URL}/api/agents"
    if token:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}"
        })
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def get_root_token() -> Optional[str]:
    """获取 root token"""
    secrets_path = PROJECT_DIR / "config" / "secrets.json"
    if not secrets_path.exists():
        return None
    secrets = json.loads(secrets_path.read_text())
    password = secrets.get("root_password", "admin")
    code, data = _http_post(f"{GATEWAY_URL}/api/auth/login", {"password": password})
    if code == 200:
        return data.get("token")
    return None


def restart_agent(agent_id: str, token: str) -> bool:
    """通过网关重启 Agent"""
    code, data = _http_post(
        f"{GATEWAY_URL}/api/agents/{agent_id}/restart", token=token
    )
    return code == 200 and data.get("ok")


def notify_feishu(message: str):
    """通过飞书 Agent 发送通知"""
    try:
        _http_post("http://localhost:8501/send", {"text": message}, timeout=15)
    except Exception as e:
        logger.warning(f"飞书通知失败: {e}")


def run_check(mode: str = "lite") -> dict:
    """执行一轮完整检查

    Args:
        mode: "lite" 或 "live"

    Returns:
        检查结果 dict，同时持久化到 health_log.json
    """
    timestamp = datetime.now().isoformat()
    logger.info(f"开始 {mode} 检查...")

    # 获取 root token
    token = get_root_token()
    if not token:
        logger.error("无法获取 root token，跳过检查")
        return {"timestamp": timestamp, "error": "无法获取 root token"}

    agents = get_agents_from_gateway(token)
    if not agents:
        logger.warning("无法获取 Agent 列表")
        return {"timestamp": timestamp, "error": "无法获取 Agent 列表"}

    results = []
    all_ok = True
    issues = []

    for agent in agents:
        aid = agent["id"]
        port = agent.get("port")
        status = agent.get("status")

        if aid == "watchdog":
            continue  # 不检查自己

        agent_result = {
            "agent_id": aid,
            "port": port,
            "gateway_status": status,
            "checks": [],
        }

        # 1. /health 检查
        if port and status == "running":
            health = check_agent_health(aid, port)
            agent_result["checks"].append(health)
            if not health["ok"]:
                all_ok = False
                issues.append(f"{aid}: health 检查失败")
        elif status != "running":
            agent_result["checks"].append({
                "check": "health", "ok": False, "detail": f"状态为 {status}"
            })
            all_ok = False
            issues.append(f"{aid}: 状态为 {status}")

        # 2. test_e2e 检查
        if port and status == "running":
            e2e = check_agent_e2e(aid, port, mode)
            agent_result["checks"].append(e2e)
            if not e2e["ok"]:
                all_ok = False
                issues.append(f"{aid}: e2e({mode}) 失败 ({e2e.get('failed', '?')} 个)")

        agent_result["ok"] = all(c["ok"] for c in agent_result["checks"])
        results.append(agent_result)

    # 汇总
    check_result = {
        "timestamp": timestamp,
        "mode": mode,
        "all_ok": all_ok,
        "agents_checked": len(results),
        "agents": results,
    }

    # 持久化
    _save_log(check_result)

    # 通知
    if not all_ok:
        msg = f"⚠️ Watchdog {mode} 检查异常\n时间: {timestamp}\n问题:\n" + "\n".join(f"  • {i}" for i in issues)
        notify_feishu(msg)
        logger.warning(f"检查异常: {issues}")

        # Level 1 自动修复：重启失败的 Agent
        for r in results:
            if not r["ok"] and r["gateway_status"] in ("running", "error"):
                aid = r["agent_id"]
                logger.info(f"尝试重启 {aid}...")
                if restart_agent(aid, token):
                    import time
                    time.sleep(5)
                    # 重新检查
                    recheck = check_agent_health(aid, r["port"])
                    if recheck["ok"]:
                        notify_feishu(f"✅ {aid} 已自动重启恢复")
                        logger.info(f"{aid} 重启后恢复")
                    else:
                        notify_feishu(f"❌ {aid} 重启后仍异常，需手动处理")
                        logger.error(f"{aid} 重启后仍异常")
    else:
        logger.info(f"检查完成: 全部正常 ({len(results)} 个 Agent)")

    return check_result


def _save_log(result: dict):
    """追加检查记录到 health_log.json"""
    HEALTH_LOG.parent.mkdir(parents=True, exist_ok=True)
    logs = []
    if HEALTH_LOG.exists():
        try:
            logs = json.loads(HEALTH_LOG.read_text())
        except Exception:
            logs = []

    logs.append(result)
    # 只保留最近 200 条
    if len(logs) > 200:
        logs = logs[-200:]

    HEALTH_LOG.write_text(json.dumps(logs, indent=2, ensure_ascii=False, default=str))


def get_health_logs(limit: int = 50) -> list:
    """读取最近的健康检查记录"""
    if not HEALTH_LOG.exists():
        return []
    try:
        logs = json.loads(HEALTH_LOG.read_text())
        return logs[-limit:]
    except Exception:
        return []
