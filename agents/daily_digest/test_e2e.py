"""Daily Digest Agent 端到端自测

三种模式：
  python3 test_e2e.py [--port 8502]            默认：接口格式校验（秒级）
  python3 test_e2e.py [--port 8502] --lite      lite：+ 标准样例单实验查询（~10s）
  python3 test_e2e.py [--port 8502] --live      full：+ 标准样例 + 全量 batch（数分钟）
"""

import sys
import json
import urllib.request
import urllib.error

PORT = 8502
MODE = "default"  # default / lite / live
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


# ========== 默认模式：接口格式校验（秒级） ==========

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
            check("包含全量拉取", "全量拉取" in html)
            check("包含单实验查询", "单实验查询" in html)
            check("包含地区选择", "地区" in html)
    except Exception as e:
        check("GET / 可达", False, str(e))


def test_experiment_validation():
    print("\n=== POST /experiment (参数校验) ===")
    code, _ = request("POST", "/experiment", {})
    check("缺少 flight_id 返回 422", code == 422, f"status={code}")

    code, _ = request("POST", "/experiment", {"flight_id": 999999})
    check("有 flight_id 不返回 422", code != 422, f"status={code}")


# ========== lite 模式：+ 标准样例单实验（~10s） ==========

def test_live_single_experiment():
    """标准测试样例: flight_id=71879109, 2026-03-16 ~ 2026-03-19, region=ROW"""
    print("\n=== POST /experiment (标准样例: 71879109 ROW) ===")
    code, data = request("POST", "/experiment", {
        "flight_id": 71879109,
        "start_date": "2026-03-16",
        "end_date": "2026-03-19",
        "data_region": "ROW",
    })
    check("返回 200", code == 200, f"status={code}")
    if code != 200:
        return

    check("flight_id 正确", data.get("flight_id") == 71879109, f"got {data.get('flight_id')}")
    check("有实验名称", bool(data.get("name")), f"name={data.get('name')}")
    check("有 url", bool(data.get("url")), f"url={data.get('url')}")
    check("start_date=2026-03-16", data.get("start_date") == "2026-03-16", f"got {data.get('start_date')}")
    check("end_date=2026-03-19", data.get("end_date") == "2026-03-19", f"got {data.get('end_date')}")

    versions = data.get("versions_results", [])
    check("有版本数据", len(versions) > 0, f"versions_results={len(versions)}")

    if versions:
        vr = versions[0]
        check("版本格式正确", isinstance(vr, list) and len(vr) == 2, f"type={type(vr)}")
        if isinstance(vr, list) and len(vr) == 2:
            vname, groups = vr
            check("版本有名称", bool(vname))
            check("版本有指标组", len(groups) > 0, f"groups={len(groups)}")
            if groups:
                g = groups[0]
                check("指标组有 group_name", bool(g.get("group_name")))
                check("指标组有指标", len(g.get("metrics", [])) > 0)
                has_data = any(
                    m.get("rel_diff") is not None
                    for gr in groups for m in gr.get("metrics", [])
                )
                check("有有效指标数据", has_data, "所有 rel_diff 为 null")

    check("状态为 ok", data.get("status") == "ok", f"status={data.get('status')}")
    print(f"  实验名: {data.get('name')}")
    print(f"  版本数: {len(versions)}")


# ========== live 模式：+ 全量 batch（数分钟） ==========

def test_live_digest():
    print("\n=== POST /digest (全量拉取，可能需要数分钟) ===")
    code, data = request("POST", "/digest", {}, timeout=600)
    check("返回 200", code == 200, f"status={code}")
    if code == 200:
        check("有 date 字段", "date" in data)
        check("有 experiments 列表", isinstance(data.get("experiments"), list))
        check("有 summary_stats", "summary_stats" in data)
        stats = data.get("summary_stats", {})
        check("total >= 0", stats.get("total", -1) >= 0)
        print(f"  结果: {stats.get('ok', 0)} 个有数据, {stats.get('not_ready', 0)} 个未就绪")


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
        print(f"ERROR: Daily Digest Agent 未运行在端口 {PORT}")
        sys.exit(1)

    # 默认模式（秒级）
    cookies_ok = test_health()
    test_index()
    test_experiment_validation()

    # lite 模式：+ 标准样例（~10s）
    if MODE in ("lite", "live") and cookies_ok:
        test_live_single_experiment()
    elif MODE in ("lite", "live"):
        print("\n  [SKIP] cookies 未配置，跳过 live 测试")

    # live 模式：+ 全量 batch（数分钟）
    if MODE == "live" and cookies_ok:
        test_live_digest()

    mode_label = {"default": "默认", "lite": "lite（含标准样例）", "live": "full（含全量 batch）"}
    print(f"\n{'='*40}")
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print(f"模式: {mode_label[MODE]}")
    print(f"{'='*40}")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
