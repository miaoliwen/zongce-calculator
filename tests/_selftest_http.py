# -*- coding: utf-8 -*-
"""
纯 HTTP 引擎离线自测：用 ThreadingHTTPServer 模拟超星扫码 + 成绩接口，
不需要真实教务系统、不需要浏览器。虚构数据与 _selftest_fixture.js 对齐。

运行（仓库根目录）：python -m tests._selftest_http
"""

import asyncio
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------- #
# 虚构成绩数据（与 _selftest_fixture.js 一致；非真实成绩单）
# --------------------------------------------------------------------------- #
ROWS = [
    ("2025-2026-2", "CS1001", "程序设计基础", "82", 4),
    ("2025-2026-2", "CS1002", "数据库原理", "76", 4),
    ("2025-2026-2", "CS1003", "计算机网络", "88", 4),
    ("2025-2026-2", "GE2001", "思想道德与法治", "81", 3),
    ("2025-2026-2", "GE2002", "军事理论", "90", 1),
    ("2025-2026-2", "PE1001", "体育", "85.5", 2),
    ("2025-2026-2", "CS1004", "数据结构与算法", "78", 4),
    ("2025-2026-2", "GE2003", "大学英语", "83.5", 4),
    ("2025-2026-1", "MA1001", "高等数学", "74", 4),
    ("2025-2026-1", "CS1005", "操作系统", "69", 3),
    ("2025-2026-1", "CS1006", "Python程序设计", "91", 4),
    ("2025-2026-1", "GE2004", "艺术鉴赏", "95.5", 1),
]


def grid_rows(lo, hi):
    out = []
    for xnxq, code, name, cj, xf in ROWS:
        if (not lo or xnxq >= lo) and (not hi or xnxq <= hi):
            out.append({
                "xnxq": xnxq,
                "kcmc": "[" + code + "]" + name,
                "zhcj": cj,
                "hdxf": str(xf),
                "xf": "",
                "fxcj": "",
            })
    return out


SCAN_PAGE = """<!DOCTYPE html><html><body>
<input type="hidden" value="UUID123456" id="uuid"/>
<input type="hidden" value="ENC654321" id="enc"/>
<input type="hidden" value="{base}/admin/scanLogin?" id="pcrefer"/>
<img src="/createqr?uuid=UUID123456" id="ewm"/>
</body></html>"""

CONTENT_PAGE = """<!DOCTYPE html><html><body>
<tr><td>起始学年学期
<select id="startXnxq" name="xnxq">
  <option value="001">入学以来</option>
  <option value="2025-2026-1">2025-2026-1</option>
  <option value="2025-2026-2">2025-2026-2</option>
</select></td></tr>
<tr><td>终止学年学期
<select id="endXnxq" name="xnxq2">
  <option value="001">入学以来</option>
  <option value="2025-2026-1">2025-2026-1</option>
  <option value="2025-2026-2">2025-2026-2</option>
</select></td></tr>
<script>
$("#xsdcjcxGridIdGrid").jqGrid({{
  url: '/admin/xsd/xsdcjcx/xsdQueryXscjList?gridtype=1', mtype: 'POST'
}});
</script>
</body></html>"""


class MockHandler(BaseHTTPRequestHandler):
    auth_calls = 0

    def log_message(self, *a):
        pass

    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        if "cloudscanlogin" in p.path:
            self._send(200, SCAN_PAGE.format(base=f"http://127.0.0.1:{self.server.server_port}"))
        elif p.path == "/createqr":
            self._send(200, b"\x89PNG\r\n\x1a\n" + b"FAKE_QR_BYTES" * 20, "image/png")
        elif p.path == "/admin/scanLogin":
            self._send(303, b"", extra_headers=[("Location", "/admin/indexMain/M1402"),
                                                ("Set-Cookie", "JWSESSIONID=abc; Path=/")])
        elif p.path == "/admin/indexMain/M1402":
            self._send(200, "<html>portal</html>")
        elif "qbcjcx" in p.path:
            self._send(200, CONTENT_PAGE)
        else:
            self._send(404, "not found: " + p.path)

    def do_POST(self):
        p = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        if p.path == "/getauthstatus":
            MockHandler.auth_calls += 1
            n = MockHandler.auth_calls
            if n <= 2:
                self._send(200, "{}", "application/json")
            elif n == 3:
                self._send(200, '{"type":4,"nickname":"\\u5c0f\\u660e","uid":"123"}',
                           "application/json")
            else:
                self._send(200, '{"status":1}', "application/json",
                           extra_headers=[("Set-Cookie", "UID=1; Domain=.127.0.0.1")])
        elif "xsdQueryXscjList" in p.path:
            lo, hi = form.get("xnxq", ""), form.get("xnxq2", "")
            lo = "" if lo in ("001", "") else lo
            hi = "" if hi in ("001", "") else hi
            rows = grid_rows(lo, hi)
            import json
            payload = {"page": int(form.get("page", 1)), "total": 1,
                       "records": len(rows), "rows": rows}
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json")
        else:
            self._send(404, "not found")


# --------------------------------------------------------------------------- #
# 测试
# --------------------------------------------------------------------------- #
FAILURES = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


async def run_tests():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    tmp = tempfile.mkdtemp(prefix="zc_http_test_")
    # 关键：通过环境变量把两个主机指向 mock（必须在 import 引擎前设置）
    os.environ["ZC_BASE_URL"] = base
    os.environ["ZC_PASSPORT_URL"] = base

    import importlib
    import crawler
    import http_crawler
    importlib.reload(crawler)
    importlib.reload(http_crawler)

    eng = http_crawler.HttpGradeCrawler(debug_dir=tmp, log=lambda m: print("   log:", m))
    await eng.start()
    check("start 后 browser 标记存在", eng.browser is not None)

    await eng.open_scan_page()
    check("二维码字节已获取", bool(eng.qr_bytes))
    check("uuid 解析正确", eng._uuid == "UUID123456")

    # 轮询：waiting → waiting → scanned → confirmed
    states = []
    for _ in range(6):
        st = await eng.scan_state()
        states.append(st["state"])
        if st["state"] == "confirmed":
            break
    check("状态序列含 waiting", "waiting" in states)
    check("状态序列含 scanned", "scanned" in states)
    check("最终 confirmed", states[-1] == "confirmed")

    filters = await eng.discover_filters()
    check("识别为起止区间模式", filters["semester_pair"] is True)
    check("学年下拉存在", filters["year"] is not None)
    check("学期下拉存在", filters["term"] is not None)
    check("选项含 2025-2026-2",
          any(o["value"] == "2025-2026-2" for o in filters["year"]["options"]))

    # 单学期抓取
    rows = await eng.crawl("2025-2026-2", "2025-2026-2", filters)
    check("2025-2026-2 返回 7 门（8 门剔除体育）", len(rows) == 7)
    check("全部属于 2025-2026 学年第 2 学期",
          all(r["year"] == "2025-2026" and r["term"] == "2" for r in rows))
    check("体育已被剔除", all("体育" not in (r["name"] or "") for r in rows))
    check("剔除项含体育",
          any("体育" in (r.get("name") or "") for r in eng.last_excluded))
    sample = next((r for r in rows if r["code"] == "CS1001"), None)
    check("成绩/学分归一化正确", sample and sample["score"] == 82 and sample["credit"] == 4)

    # 全部学期（用页面自带 001 入学以来）
    MockHandler.auth_calls = 99          # 避免再碰扫码状态机
    eng2 = http_crawler.HttpGradeCrawler(debug_dir=tmp, log=lambda m: None)
    await eng2.start()
    eng2._uuid = "UUID123456"; eng2._enc = "ENC654321"
    eng2._pcrefer = base + "/admin/scanLogin?"
    eng2._started_at = __import__("time").time()
    all_rows = await eng2.crawl("__ALL__", "__ALL__", None)
    check("入学以来返回 11 门（12 门剔除体育）", len(all_rows) == 11)
    check("跨学期数据齐全",
          {r["term"] for r in all_rows} == {"1", "2"})

    await eng.close()
    check("close 后 browser 标记清除", eng.browser is None)

    server.shutdown()


def main():
    print("== 纯 HTTP 引擎离线自测 ==")
    asyncio.run(run_tests())
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败：{FAILURES}")
        sys.exit(1)
    print("\n全部断言通过。")


if __name__ == "__main__":
    main()
