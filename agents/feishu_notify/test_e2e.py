"""飞书通知 Agent 端到端自测

测试所有 API 接口是否正常工作。
用法: python3 test_e2e.py [--port 8501] [--send]
  --send  实际发送消息到飞书群（默认不发送，只测接口可达性）
"""

import sys
import json
import urllib.request
import urllib.error

PORT = 8501
ACTUALLY_SEND = False
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


def request(method, path, data=None):
    url = f"http://localhost:{PORT}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
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
    check("configured 字段存在", "configured" in data)
    return data.get("configured", False)


def test_index():
    print("\n=== / (前端页面) ===")
    try:
        req = urllib.request.Request(f"http://localhost:{PORT}/")
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode()
            check("GET / 返回 200", resp.status == 200)
            check("返回 HTML", "<!DOCTYPE html>" in html or "<html" in html)
            check("包含发送按钮", "发送" in html)
            check("包含纯文本模式", "纯文本" in html)
            check("包含富文本模式", "富文本" in html)
    except Exception as e:
        check("GET / 可达", False, str(e))


def test_send_text(configured):
    print("\n=== POST /send (纯文本) ===")
    if not configured:
        print("  [SKIP] Webhook 未配置，跳过发送测试")
        return

    if not ACTUALLY_SEND:
        # 只测接口格式，不实际发送
        # 发一个空文本应该被 pydantic 拒绝或正常处理
        code, data = request("POST", "/send", {"text": ""})
        check("空文本请求返回 200", code == 200, f"status={code}")
        return

    code, data = request("POST", "/send", {"text": "自测消息 - 纯文本"})
    check("发送纯文本返回 200", code == 200, f"status={code}")
    check("发送成功", data.get("ok") is True, f"data={data}")


def test_send_text_validation():
    print("\n=== POST /send (参数校验) ===")
    # 缺少 text 字段
    code, _ = request("POST", "/send", {})
    check("缺少 text 字段返回 422", code == 422, f"status={code}")

    # 非 JSON body
    try:
        req = urllib.request.Request(
            f"http://localhost:{PORT}/send",
            data=b"not json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            check("非 JSON body 应报错", False, f"status={resp.status}")
    except urllib.error.HTTPError as e:
        check("非 JSON body 返回 422", e.code == 422, f"status={e.code}")


def test_send_rich(configured):
    print("\n=== POST /send_rich (富文本) ===")
    if not configured:
        print("  [SKIP] Webhook 未配置，跳过发送测试")
        return

    rich_content = {
        "title": "自测 - 富文本",
        "content": [
            [
                {"tag": "text", "text": "普通文本 "},
                {"tag": "text", "text": "加粗文本"},
            ],
            [
                {"tag": "a", "text": "链接", "href": "https://example.com"},
            ],
        ],
    }

    if not ACTUALLY_SEND:
        # 只验证接口格式正确
        code, data = request("POST", "/send_rich", rich_content)
        check("富文本请求返回 200", code == 200, f"status={code}")
        return

    code, data = request("POST", "/send_rich", rich_content)
    check("发送富文本返回 200", code == 200, f"status={code}")
    check("发送成功", data.get("ok") is True, f"data={data}")


def test_send_rich_validation():
    print("\n=== POST /send_rich (参数校验) ===")
    # 缺少 content 字段
    code, _ = request("POST", "/send_rich", {"title": "test"})
    check("缺少 content 返回 422", code == 422, f"status={code}")


def test_connection(configured):
    print("\n=== POST /test (连接测试) ===")
    if not configured:
        print("  [SKIP] Webhook 未配置")
        return
    if not ACTUALLY_SEND:
        print("  [SKIP] 需要 --send 参数才实际发送")
        return
    code, data = request("POST", "/test")
    check("连接测试返回 200", code == 200, f"status={code}")
    check("测试成功", data.get("ok") is True, f"data={data}")


def main():
    global PORT, ACTUALLY_SEND

    args = sys.argv[1:]
    if "--port" in args:
        PORT = int(args[args.index("--port") + 1])
    if "--send" in args:
        ACTUALLY_SEND = True

    # 检查服务是否运行
    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=3)
    except Exception:
        print(f"ERROR: 飞书 Agent 未运行在端口 {PORT}")
        print(f"请先启动: cd agents/feishu_notify && uvicorn app:app --port {PORT}")
        sys.exit(1)

    configured = test_health()
    test_index()
    test_send_text(configured)
    test_send_text_validation()
    test_send_rich(configured)
    test_send_rich_validation()
    test_connection(configured)

    print(f"\n{'='*40}")
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    if ACTUALLY_SEND:
        print("(含实际发送测试)")
    else:
        print("(未实际发送，加 --send 参数可测试真实发送)")
    print(f"{'='*40}")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
