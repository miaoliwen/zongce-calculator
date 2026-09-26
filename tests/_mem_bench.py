# -*- coding: utf-8 -*-
"""
内存占用基准：启动真实 Flask 子进程，分别测 http / browser 引擎的
空闲与工作内存（进程树 RSS 合计）。用 mock 站点，不需要真实教务系统。

运行（仓库根）：python -m tests._mem_bench
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import psutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests._selftest_http import MockHandler  # noqa: E402

PYTHON = r"C:\Python313\python.exe"


def http_json(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def tree_rss(proc):
    p = psutil.Process(proc.pid)
    total = p.memory_info().rss
    for c in p.children(recursive=True):
        try:
            total += c.memory_info().rss
        except Exception:
            pass
    return total


def wait_healthz(port, proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return http_json("GET", f"http://127.0.0.1:{port}/healthz")
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("server exited early")
            time.sleep(0.5)
    raise RuntimeError("healthz timeout")


def sample_for(p, seconds):
    vals = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            vals.append(tree_rss(p))
        except Exception:
            pass
        time.sleep(0.5)
    return min(vals), max(vals)


def clean_env(extra):
    # 继承当前环境但剔除工具注入的 PYTHON 隔离变量，保证用户站点包可用
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTHON") and k != "__PYVENV_LAUNCHER__"}
    env.update(extra)
    return env


def run_case(engine, app_port, mock_port, tmp):
    extra = {
        "ZC_BASE_URL": f"http://127.0.0.1:{mock_port}",
        "ZC_PASSPORT_URL": f"http://127.0.0.1:{mock_port}",
        "ZC_HEADLESS": "1",
        "ZC_ENGINE": engine,
        "ZC_HOST": "127.0.0.1",
        "ZC_PORT": str(app_port),
        "ZC_DEBUG_DIR": tmp,
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.Popen(
        [PYTHON, "app.py"], cwd=ROOT, env=clean_env(extra),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        h = wait_healthz(app_port, proc)
        idle_min, idle_max = sample_for(proc, 3)

        peak = idle_max
        if engine == "http":
            MockHandler.auth_calls = 0
            http_json("POST", f"http://127.0.0.1:{app_port}/api/scan/start", {})
            while True:
                st = http_json("GET", f"http://127.0.0.1:{app_port}/api/scan/state")
                _, hi = sample_for(proc, 0.6)
                peak = max(peak, hi)
                if st["state"] == "confirmed":
                    break
            http_json("POST", f"http://127.0.0.1:{app_port}/api/options", {})
            http_json("POST", f"http://127.0.0.1:{app_port}/api/crawl",
                      {"year": "2025-2026-2", "term": "2025-2026-2"})
            _, peak_work = sample_for(proc, 2)
            peak = max(peak, peak_work)
        else:
            # browser：完整启动 Chromium 并打开扫码页（成绩页只会更重）
            http_json("POST", f"http://127.0.0.1:{app_port}/api/scan/start", {})
            time.sleep(6)
            _, peak = sample_for(proc, 4)

        children = [c.name() for c in psutil.Process(proc.pid).children(recursive=True)]
        return {"engine": h.get("engine"), "idle_mb": round(idle_min / 1e6, 1),
                "peak_mb": round(peak / 1e6, 1), "child_procs": children}
    finally:
        try:
            p = psutil.Process(proc.pid)
            for c in p.children(recursive=True):
                c.kill()
            proc.kill()
        except Exception:
            pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    mock_port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tmp = tempfile.mkdtemp(prefix="zc_mem_")

    results = []
    for engine, port in (("http", 18911), ("browser", 18912)):
        print(f"measuring {engine} ...")
        results.append(run_case(engine, port, mock_port, tmp))

    print(json.dumps(results, ensure_ascii=False, indent=2))
    server.shutdown()


if __name__ == "__main__":
    main()
