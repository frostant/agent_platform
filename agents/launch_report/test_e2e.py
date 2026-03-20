"""实验报告生成 Agent 端到端自测

三种模式：
  python3 test_e2e.py [--port 8503]            默认：接口格式校验（秒级）
  python3 test_e2e.py [--port 8503] --lite      lite：+ 标准样例爬取 2 个指标组（~15s）
  python3 test_e2e.py [--port 8503] --live      full：+ 完整爬取所有指标组
"""

import sys
import json
import urllib.request
import urllib.error

PORT = 8503
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


# ========== 默认模式 ==========

def test_health():
    print("\n=== /health ===")
    code, data = request("GET", "/health")
    check("GET /health 返回 200", code == 200, f"status={code}")
    check("status=ok", data.get("status") == "ok")
    check("cookies_configured 字段存在", "cookies_configured" in data)
    return data.get("cookies_configured", False)


def test_index():
    print("\n=== / (前端页面) ===")
    try:
        req = urllib.request.Request(f"http://localhost:{PORT}/")
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode()
            check("GET / 返回 200", resp.status == 200)
            check("返回 HTML", "<html" in html)
            check("包含爬取指标", "爬取指标" in html)
            check("包含 Flight ID", "Flight ID" in html)
    except Exception as e:
        check("GET / 可达", False, str(e))


def test_crawl_validation():
    print("\n=== POST /crawl (参数校验) ===")
    code, _ = request("POST", "/crawl", {})
    check("缺少 flight_id 返回 422", code == 422, f"status={code}")


def test_outputs():
    print("\n=== GET /outputs ===")
    code, data = request("GET", "/outputs")
    check("GET /outputs 返回 200", code == 200, f"status={code}")
    check("返回列表", isinstance(data, list), f"type={type(data)}")


# ========== lite 模式：标准样例爬取 ==========

def test_lite_crawl():
    """标准样例: flight_id=71879109, 2026-03-16 ~ 2026-03-19"""
    print("\n=== POST /crawl (标准样例: 71879109) ===")
    code, data = request("POST", "/crawl", {
        "flight_id": 71879109,
        "start_date": "2026-03-16",
        "end_date": "2026-03-19",
    }, timeout=60)
    check("返回 200", code == 200, f"status={code}")
    if code != 200:
        return

    check("ok=True", data.get("ok") is True)
    check("有 output 路径", bool(data.get("output")))

    result = data.get("data", {})
    check("有 experiment_name", bool(result.get("experiment_name")))
    check("有 base_vname", bool(result.get("base_vname")))
    check("有 target_vname", bool(result.get("target_vname")))
    check("有 groups 数据", bool(result.get("groups")))

    groups = result.get("groups", {})
    ok_groups = [g for g in groups.values() if "error" not in g]
    check("至少 2 个指标组成功", len(ok_groups) >= 2, f"成功 {len(ok_groups)} 个")

    # 检查指标数据
    has_metrics = False
    for g in ok_groups:
        for m in g.get("metrics", []):
            if m.get("rel_diff") is not None:
                has_metrics = True
                break
    check("有有效指标数据", has_metrics)

    print(f"  实验名: {result.get('experiment_name')}")
    print(f"  版本: {result.get('target_vname')}")
    print(f"  指标组: {len(groups)} 个 ({len(ok_groups)} 成功)")


# ========== 入口 ==========

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
        print(f"ERROR: Launch Report Agent 未运行在端口 {PORT}")
        sys.exit(1)

    cookies_ok = test_health()
    test_index()
    test_crawl_validation()
    test_outputs()

    if MODE in ("lite", "live") and cookies_ok:
        test_lite_crawl()
    elif MODE in ("lite", "live"):
        print("\n  [SKIP] cookies 未配置")

    mode_label = {"default": "默认", "lite": "lite（含标准样例）", "live": "full"}
    print(f"\n{'='*40}")
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print(f"模式: {mode_label[MODE]}")
    print(f"{'='*40}")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
