"""网关自测：注册发现、认证、API 接口"""

import sys
import json
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def get(path, token=""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def post(path, data=None, token=""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else b""
    req = urllib.request.Request(f"{BASE}{path}", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}
    except Exception as e:
        return 0, {"error": str(e)}


def test_health():
    print("\n=== 健康检查 ===")
    code, data = get("/api/health")
    check("GET /api/health 返回 200", code == 200, f"status={code}")
    check("status=ok", data.get("status") == "ok", f"data={data}")
    check("agents 字段存在", "agents" in data, f"data={data}")


def test_agent_list():
    print("\n=== Agent 列表（guest）===")
    code, data = get("/api/agents")
    check("GET /api/agents 返回 200", code == 200, f"status={code}")
    check("返回列表", isinstance(data, list), f"type={type(data)}")
    if isinstance(data, list) and len(data) > 0:
        agent = data[0]
        check("Agent 有 id 字段", "id" in agent)
        check("Agent 有 name 字段", "name" in agent)
        check("Agent 有 status 字段", "status" in agent)
        check("Agent 有 tags 字段", "tags" in agent)
        # guest 不应看到 root_only Agent
        root_only = [a for a in data if a.get("access") == "root_only"]
        check("guest 看不到 root_only Agent", len(root_only) == 0,
              f"found {len(root_only)} root_only agents")


def test_auth():
    print("\n=== 认证 ===")
    # 错误密码
    code, _ = post("/api/auth/login", {"password": "wrong_password"})
    check("错误密码返回 401", code == 401, f"status={code}")

    # 正确密码
    code, data = post("/api/auth/login", {"password": "admin"})
    check("正确密码返回 200", code == 200, f"status={code}")
    token = data.get("token", "")
    check("返回 token", bool(token), f"data={data}")

    # 验证 token
    code, data = get("/api/auth/me", token=token)
    check("GET /api/auth/me 返回 200", code == 200, f"status={code}")
    check("角色为 root", data.get("role") == "root", f"data={data}")

    return token


def test_agent_management(token):
    print("\n=== Agent 管理（root）===")
    # 获取 Agent 列表
    code, agents = get("/api/agents", token=token)
    check("root 获取 Agent 列表", code == 200)

    if not agents:
        print("  [SKIP] 无 Agent，跳过管理测试")
        return

    agent_id = agents[0]["id"]

    # 获取单个 Agent
    code, data = get(f"/api/agents/{agent_id}", token=token)
    check(f"GET /api/agents/{agent_id} 返回 200", code == 200)
    check("返回正确 Agent", data.get("id") == agent_id)

    # 不存在的 Agent
    code, _ = get("/api/agents/nonexistent", token=token)
    check("不存在的 Agent 返回 404", code == 404, f"status={code}")

    # guest 不能启停
    code, _ = post(f"/api/agents/{agent_id}/restart")
    check("guest 不能重启 Agent (403)", code == 403, f"status={code}")

    # root 重启
    code, data = post(f"/api/agents/{agent_id}/restart", token=token)
    check("root 可以重启 Agent", code == 200, f"status={code}, data={data}")


def test_agent_service():
    """测试 Agent 子服务是否正常响应（带重试，等子进程启动）"""
    import time
    print("\n=== Agent 子服务 ===")
    code, agents = get("/api/agents")
    for agent in (agents if isinstance(agents, list) else []):
        port = agent.get("port")
        if not port or agent.get("status") != "running":
            continue
        ok = False
        health = {}
        for attempt in range(5):
            try:
                req = urllib.request.Request(f"http://localhost:{port}/health")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    health = json.loads(resp.read())
                    ok = True
                    break
            except Exception:
                time.sleep(2)
        check(f"{agent['id']} (:{port}) /health 可达", ok,
              "5 次重试后仍不可达" if not ok else "")
        if ok:
            check(f"{agent['id']} status=ok", health.get("status") == "ok")


def main():
    global PASS, FAIL

    # 检查网关是否运行
    try:
        urllib.request.urlopen(f"{BASE}/api/health", timeout=3)
    except Exception:
        print("ERROR: 网关未运行，请先启动: python3 -m uvicorn gateway.main:app --port 8000")
        sys.exit(1)

    test_health()
    test_agent_list()
    token = test_auth()
    test_agent_management(token)
    test_agent_service()

    print(f"\n{'='*40}")
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print(f"{'='*40}")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
