"""Watchdog Agent 端到端自测

三种模式：
  python3 test_e2e.py [--port 8510]            默认：接口格式校验
  python3 test_e2e.py [--port 8510] --lite      lite：+ 触发一次 lite 检查
  python3 test_e2e.py [--port 8510] --live      full：+ 触发一次 live 检查
"""

import sys
import json
import urllib.request
import urllib.error

PORT = 8510
MODE = "default"
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


def request(method, path, data=None, timeout=120):
    url = f"http://localhost:{PORT}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
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


def test_health():
    print("\n=== /health ===")
    code, data = request("GET", "/health")
    check("GET /health 返回 200", code == 200, f"status={code}")
    check("status=ok", data.get("status") == "ok")


def test_index():
    print("\n=== / (前端看板) ===")
    try:
        req = urllib.request.Request(f"http://localhost:{PORT}/")
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode()
            check("GET / 返回 200", resp.status == 200)
            check("包含心跳守护", "心跳守护" in html)
            check("包含检查按钮", "Lite 检查" in html)
    except Exception as e:
        check("GET / 可达", False, str(e))


def test_logs():
    print("\n=== GET /logs ===")
    code, data = request("GET", "/logs?limit=5")
    check("GET /logs 返回 200", code == 200, f"status={code}")
    check("返回列表", isinstance(data, list), f"type={type(data)}")


def test_status():
    print("\n=== GET /status ===")
    code, data = request("GET", "/status")
    check("GET /status 返回 200", code == 200, f"status={code}")


def test_check(mode):
    print(f"\n=== POST /check ({mode}) ===")
    code, data = request("POST", "/check", {"mode": mode}, timeout=600)
    check("返回 200", code == 200, f"status={code}")
    if code == 200:
        check("有 timestamp", bool(data.get("timestamp")))
        check("有 agents", isinstance(data.get("agents"), list))
        check("有 all_ok 字段", "all_ok" in data)
        ok_count = sum(1 for a in data.get("agents", []) if a.get("ok"))
        total = len(data.get("agents", []))
        print(f"  结果: {ok_count}/{total} 个 Agent 正常")


def main():
    global PORT, MODE

    args = sys.argv[1:]
    if "--port" in args:
        PORT = int(args[args.index("--port") + 1])
    if "--live" in args:
        MODE = "live"
    elif "--lite" in args:
        MODE = "lite"

    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=3)
    except Exception:
        print(f"ERROR: Watchdog Agent 未运行在端口 {PORT}")
        sys.exit(1)

    test_health()
    test_index()
    test_logs()
    test_status()

    if MODE in ("lite", "live"):
        test_check(MODE)

    mode_label = {"default": "默认", "lite": "lite（含检查）", "live": "full（含检查）"}
    print(f"\n{'='*40}")
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print(f"模式: {mode_label[MODE]}")
    print(f"{'='*40}")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
