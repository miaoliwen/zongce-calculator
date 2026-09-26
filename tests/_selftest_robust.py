# -*- coding: utf-8 -*-
"""
鲁棒性专项自测
================
引擎层（同进程）：
  1. 扫码页 503 / 二维码 500 → 有限重试后成功
  2. 轮询接口返回非 JSON（风控/验证页）→ 前若干次静默 waiting，连续失败才 error
  3. health() 正常/失效判定
  4. ensure_engine 检测到假活引擎（health False）→ 自动关闭并重建
服务层（真实子进程）：
  5. 未知路径 → 404 JSON；超大请求体 → 413 JSON；畸形 JSON 不崩溃
  6. 轮询连续失败 → scan/state 返回 error

运行（仓库根）：python -m tests._selftest_robust
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests import _selftest_http as base   # noqa: E402

PYTHON = r"C:\Python313\python.exe"
FAILURES = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------------------------- #
# 可控故障的 mock 站点
# --------------------------------------------------------------------------- #
class RobustHandler(BaseHTTPRequestHandler):
    # 类级配置（每个 server 实例前重置）
    cloud_fail = 0
    qr_fail = 0
    auth_html = False
    auth_calls = 0
    cloud_calls = 0
    qr_calls = 0

    def log_message(self, *a):
        pass

    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        if "cloudscanlogin" in p.path:
            RobustHandler.cloud_calls += 1
            if RobustHandler.cloud_calls <= RobustHandler.cloud_fail:
                self._send(503, "busy")
                return
            self._send(200, base.SCAN_PAGE.format(
                base=f"http://127.0.0.1:{self.server.server_port}"))
        elif p.path == "/createqr":
            RobustHandler.qr_calls += 1
            if RobustHandler.qr_calls <= RobustHandler.qr_fail:
                self._send(500, "qr busy")
                return
            self._send(200, b"\x89PNG\r\n\x1a\n" + b"FAKE_QR_BYTES" * 20, "image/png")
        elif p.path == "/admin/scanLogin":
            self._send(303, b"", extra=[("Location", "/admin/indexMain/M1402"),
                                        ("Set-Cookie", "JW=abc; Path=/")])
        elif p.path == "/admin/indexMain/M1402":
            self._send(200, "<html>portal</html>")
        elif "qbcjcx" in p.path:
            self._send(200, base.CONTENT_PAGE)
        else:
            self._send(404, "not found")

    def do_POST(self):
        p = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if p.path == "/getauthstatus":
            RobustHandler.auth_calls += 1
            if RobustHandler.auth_html:
                self._send(200, "<html>verify page</html>", "text/html; charset=utf-8")
                return
            self._send(200, "{}", "application/json")
        elif "xsdQueryXscjList" in p.path:
            payload = {"page": 1, "total": 1, "records": 0, "rows": []}
            self._send(200, json.dumps(payload), "application/json")
        else:
            self._send(404, "not found")


def new_server():
    RobustHandler.cloud_fail = RobustHandler.qr_fail = 0
    RobustHandler.auth_html = False
    RobustHandler.auth_calls = RobustHandler.cloud_calls = RobustHandler.qr_calls = 0
    s = ThreadingHTTPServer(("127.0.0.1", 0), RobustHandler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


# --------------------------------------------------------------------------- #
# Part 1：引擎层
# --------------------------------------------------------------------------- #
async def engine_tests():
    print("-- Part 1：引擎层重试 / 容错 / 自愈 --")
    server = new_server()
    port = server.server_port
    mock = f"http://127.0.0.1:{port}"
    tmp = tempfile.mkdtemp(prefix="zc_rob_")
    os.environ["ZC_BASE_URL"] = mock
    os.environ["ZC_PASSPORT_URL"] = mock

    import importlib
    import crawler
    import http_crawler
    importlib.reload(crawler)
    importlib.reload(http_crawler)

    # 1) 扫码页 2 次 503 + 二维码 1 次 500 → 重试成功
    RobustHandler.cloud_fail = 2
    RobustHandler.qr_fail = 1
    eng = http_crawler.HttpGradeCrawler(debug_dir=tmp, log=lambda m: print("   log:", m))
    await eng.start()
    await eng.open_scan_page()
    check("扫码页/二维码 5xx 重试后成功", bool(eng.qr_bytes))
    # 预热(start)与打开扫码页各请求一次：2 次 503 + 2 次成功 = 4
    check("扫码页恰好经历 2 次失败后恢复", RobustHandler.cloud_calls == 4)
    check("二维码恰好经历 1 次失败", RobustHandler.qr_calls == 2)

    # 2) 轮询返回非 JSON：前 4 次 waiting，第 5 次 error
    RobustHandler.auth_html = True
    states = [(await eng.scan_state())["state"] for _ in range(5)]
    check("非 JSON 轮询前 4 次静默 waiting", states[:4] == ["waiting"] * 4)
    check("连续失败 5 次上报 error", states[4] == "error")

    # 3) health
    check("正常引擎 health=True", await eng.health())
    await eng.close()
    check("close 后 health=False", not await eng.health())

    # 4) ensure_engine 自愈：假活引擎（health False）被关闭并重建
    RobustHandler.cloud_fail = 0
    RobustHandler.qr_fail = 0
    RobustHandler.auth_html = False

    import app as appmod
    importlib.reload(appmod)
    sess = appmod.manager.session("robusttest")

    class _Fake:
        def __init__(self):
            self.browser = object()
            self.closed = False

        async def health(self):
            return False

        async def close(self):
            self.closed = True

    fake = _Fake()
    sess.engine = fake
    rebuilt = await appmod.manager.ensure_engine(sess)
    check("假活引擎被 close", fake.closed)
    check("引擎已重建为真实实例", rebuilt is not fake and rebuilt is not None)
    check("重建后引擎 health=True", await rebuilt.health())
    await appmod.manager.ensure_engine(sess) and None
    await rebuilt.close()
    sess.engine = None

    server.shutdown()


# --------------------------------------------------------------------------- #
# Part 2：服务层（真实子进程）
# --------------------------------------------------------------------------- #
def raw_request(port, method, path, body=None, ctype="application/json"):
    headers = {"Content-Type": ctype}
    data = body if isinstance(body, bytes) else (body.encode() if body else None)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def service_tests():
    print("-- Part 2：服务层 JSON 错误处理 / 轮询容错 --")
    server = new_server()
    mock_port = server.server_port
    app_port = 18933
    tmp = tempfile.mkdtemp(prefix="zc_rob_svc_")

    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTHON") and k != "__PYVENV_LAUNCHER__"}
    env.update({
        "ZC_BASE_URL": f"http://127.0.0.1:{mock_port}",
        "ZC_PASSPORT_URL": f"http://127.0.0.1:{mock_port}",
        "ZC_HEADLESS": "1",
        "ZC_ENGINE": "http",
        "ZC_HOST": "127.0.0.1",
        "ZC_PORT": str(app_port),
        "ZC_DEBUG_DIR": tmp,
        "PYTHONIOENCODING": "utf-8",
    })
    proc = subprocess.Popen([PYTHON, "app.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{app_port}/healthz", timeout=3)
                break
            except Exception:
                time.sleep(0.5)

        # 5a) 未知路径 → 404 JSON
        code, body = raw_request(app_port, "GET", "/api/nope")
        payload = json.loads(body)
        check("未知 API 返回 404", code == 404)
        check("404 响应为 JSON（ok:false）", payload.get("ok") is False)

        # 5b) 超大请求体 → 413 JSON
        code, body = raw_request(app_port, "POST", "/api/crawl", body=b"x" * (3 * 1024 * 1024))
        payload = json.loads(body)
        check("超大请求体返回 413", code == 413)
        check("413 响应为 JSON", payload.get("ok") is False)

        # 5c) 畸形 JSON 不崩溃（silent 解析 → 业务校验返回 400 请先登录）
        code, body = raw_request(app_port, "POST", "/api/crawl", body=b"{bad json")
        payload = json.loads(body)
        check("畸形 JSON 不产生 500", code == 400)
        check("畸形 JSON 走业务错误提示", "扫码登录" in payload.get("error", ""))

        # 6) 轮询连续失败 → error
        RobustHandler.auth_html = True
        code, _ = raw_request(app_port, "POST", "/api/scan/start", body=b"{}")
        check("扫码启动成功", code == 200)
        last = None
        for _ in range(6):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{app_port}/api/scan/state", timeout=15
            ) as r:
                last = json.loads(r.read())
        check("服务端轮询连续失败上报 error", last and last.get("state") == "error")
    finally:
        try:
            import psutil
            p = psutil.Process(proc.pid)
            for c in p.children(recursive=True):
                c.kill()
            proc.kill()
        except Exception:
            pass
        server.shutdown()


def main():
    print("== 鲁棒性专项自测 ==")
    asyncio.run(engine_tests())
    service_tests()
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败：{FAILURES}")
        sys.exit(1)
    print("\n全部断言通过。")


if __name__ == "__main__":
    main()
