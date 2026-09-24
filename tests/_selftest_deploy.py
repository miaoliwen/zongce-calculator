# -*- coding: utf-8 -*-
"""
部署改造自测：CORS 白名单 / Token 鉴权 / 无头模式 / 忙占位 / 脱敏 / 快照清理 /
             多租户（ZC_TOKENS）/ 会话与快照隔离 / 并发上限 / ZC_DEBUG_WRITE
运行：python -m tests._selftest_deploy   （在仓库根目录执行；详细结果见 tests/_selftest_deploy_out.txt）
"""
import asyncio
import importlib
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

LOG = open(HERE / "_selftest_deploy_out.txt", "w", encoding="utf-8")
FAILS = []
ENV_KEYS = ("ZC_ALLOWED_ORIGINS", "ZC_TOKEN", "ZC_TOKENS", "ZC_HEADLESS",
            "ZC_DEBUG_DIR", "ZC_IDLE_CLOSE", "ZC_DEBUG_KEEP", "ZC_MAX_ENGINES",
            "ZC_DEBUG_WRITE")


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.write(s + "\n")
    LOG.flush()


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    log(f"[{'OK ' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    return ok


def fresh_app(env, tmp_debug, idle="0"):
    """按要求的环境变量重新加载 app（配置在导入时读取）。"""
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ["ZC_DEBUG_DIR"] = str(tmp_debug)     # 默认落临时目录，避免污染真实 debug/
    if idle is not None:
        os.environ["ZC_IDLE_CLOSE"] = idle          # 测试里默认不启动空闲线程
    os.environ.update(env)                          # env 可覆盖任何一项
    mod = importlib.import_module("app")
    importlib.reload(mod)
    return mod


def main():
    tmp = Path(tempfile.mkdtemp(prefix="zc_selftest_"))

    # ---------- 1) CORS：默认只放行 file:// ----------
    log("=== 1) CORS 白名单 ===")
    a = fresh_app({}, tmp)
    c = a.app.test_client()
    r = c.get("/", headers={"Origin": "null"})
    check("默认放行 file:// (Origin: null)", r.headers.get("Access-Control-Allow-Origin"), "null")
    r = c.get("/", headers={"Origin": "https://evil.com"})
    check("恶意站点被拒", r.headers.get("Access-Control-Allow-Origin"), None)
    check("不再出现通配 *", "*" not in ", ".join(r.headers.values()), True)

    # ---------- 2) 生产：显式空白名单 = 全关 ----------
    log("\n=== 2) ZC_ALLOWED_ORIGINS=（空）===")
    a = fresh_app({"ZC_ALLOWED_ORIGINS": ""}, tmp)
    c = a.app.test_client()
    r = c.get("/", headers={"Origin": "null"})
    check("连 null 也拒绝（同源部署）", r.headers.get("Access-Control-Allow-Origin"), None)
    check("白名单为空集", a.ALLOWED_ORIGINS, set())

    # ---------- 3) Token 鉴权 ----------
    log("\n=== 3) ZC_TOKEN 鉴权 ===")
    a = fresh_app({"ZC_TOKEN": "secret123"}, tmp)
    c = a.app.test_client()
    check("无 token → 401", c.get("/api/progress").status_code, 401)
    check("错 token → 401",
          c.get("/api/progress", headers={"X-Token": "wrong"}).status_code, 401)
    check("对 token → 200",
          c.get("/api/progress", headers={"X-Token": "secret123"}).status_code, 200)
    check("页面本身不需要 token", c.get("/").status_code, 200)
    check("healthz 不需要 token", c.get("/healthz").status_code, 200)
    check("二维码图片支持 query token",
          c.get("/api/scan/qr.png?token=secret123").status_code != 401, True)
    check("二维码图片无 token → 401", c.get("/api/scan/qr.png").status_code, 401)
    check("capabilities 无需 token（否则前端不知要令牌）",
          c.get("/api/capabilities").status_code, 200)
    check("capabilities.auth_required", c.get("/api/capabilities").get_json()["auth_required"], True)

    # ---------- 4) 无头模式 ----------
    log("\n=== 4) 无头模式（服务器）===")
    a = fresh_app({"ZC_HEADLESS": "1"}, tmp)
    c = a.app.test_client()
    check("IS_HEADLESS", a.IS_HEADLESS, True)
    r = c.post("/api/scan/start", json={"mode": "window"})
    check("弹窗扫码被拒（400）", r.status_code, 400)
    check("给出可读原因", "无头" in r.get_json()["error"], True)
    d = c.get("/api/capabilities").get_json()
    check("capabilities.headed", d["headed"], False)
    check("healthz.headless", c.get("/healthz").get_json()["headless"], True)

    log("\n=== 4b) 有头模式（本机）===")
    a = fresh_app({"ZC_HEADLESS": "0"}, tmp)
    check("IS_HEADLESS", a.IS_HEADLESS, False)
    check("capabilities.headed",
          a.app.test_client().get("/api/capabilities").get_json()["headed"], True)

    # ---------- 5) 忙占位（并发保护，按用户）----------
    log("\n=== 5) 忙占位 / 并发保护 ===")
    a = fresh_app({}, tmp)
    c = a.app.test_client()
    sess = a.manager.session("default")
    check("空闲时无忙碌标记", sess.busy_label, None)
    check("占位成功", sess.try_begin("测试任务"), True)
    r = c.post("/api/crawl", json={})
    check("忙时抓取 → 409", r.status_code, 409)
    check("忙时提示可读", "你已有任务在进行中" in r.get_json()["error"], True)
    check("忙时第二次占位失败", sess.try_begin("抢锁"), False)
    sess.end()
    check("释放后 busy 清空", sess.busy_label, None)
    check("释放后可再次占位", sess.try_begin("再来"), True)
    sess.end()
    check("close 后 healthz 仍可用", c.get("/healthz").status_code, 200)
# ---------- 6) 脱敏 ----------
    log("\n=== 6) 快照脱敏 _scrub ===")
    import crawler as crm
    s = '{"currentUserName":"20240000000","currentUserId":"abc","name":"张三","mobile":"13812345678"}'
    o = crm._scrub(s)
    log("  处理后 ->", o)
    check("学号打码", "20240000000" in o, False)
    check("内部 ID 打码", '"currentUserId":"***"' in o, True)
    check("手机号打码", "13812345678" in o, False)
    check("非敏感内容保留（便于排查结构）", "张三" in o, True)
    check("空值安全", crm._scrub(""), "")

    # ---------- 7) 快照清理 ----------
    log("\n=== 7) 快照自动清理 ===")
    snap = tmp / "snaps"
    snap.mkdir(exist_ok=True)
    for i in range(4):
        (snap / ("crawl_error_2026092" + str(i) + "_000000")).mkdir(exist_ok=True)
        time.sleep(0.02)
    cr = crm.GradeCrawler(debug_dir=str(snap), log=lambda m: log("  ·", m))
    old_keep = crm.DEBUG_KEEP
    crm.DEBUG_KEEP = 2
    cr._prune_debug()
    left = sorted(p.name for p in snap.iterdir() if p.is_dir())
    log("  剩余 ->", left)
    check("只保留最近 2 个快照", len(left), 2)
    check("保留的是最新的", left[-1].endswith("3_000000"), True)
    crm.DEBUG_KEEP = 0
    cr._prune_debug()
    check("KEEP=0 表示不清理", len([p for p in snap.iterdir() if p.is_dir()]), 2)
    crm.DEBUG_KEEP = old_keep

    # ---------- 7b) ZC_DEBUG_WRITE：彻底不落盘 ----------
    log("\n=== 7b) ZC_DEBUG_WRITE 开关 ===")
    cr = crm.GradeCrawler(debug_dir=str(tmp / "dw"), log=lambda m: log("  ·", m))
    crm.DEBUG_WRITE = 1
    p1 = asyncio.run(cr.dump("on"))
    check("WRITE=1 正常落盘", Path(p1).is_dir(), True)
    crm.DEBUG_WRITE = 0
    p2 = asyncio.run(cr.dump("off"))
    check("WRITE=0 返回空路径", p2, "")
    check("WRITE=0 不产生新快照",
          [p.name for p in (tmp / "dw").iterdir() if p.is_dir()][0].startswith("on"), True)
    crm.DEBUG_WRITE = 1

    # ---------- 6b) XHR 捕获按主机过滤 ----------
    log("\n=== 6b) _on_response 主机过滤 ===")
    cr2 = crm.GradeCrawler(debug_dir=str(tmp / "cap"), log=lambda m: log("  ·", m))

    class _FakeResp:
        def __init__(self, url):
            self.url = url
            self.headers = {"content-type": "application/json"}
            self.status = 200

        async def text(self):
            return '{"data":"x"}'

    asyncio.run(cr2._on_response(
        _FakeResp("https://passport2.chaoxing.com/getauthstatus")))
    check("passport2 扫码轮询不捕获", len(cr2._captured), 0)
    asyncio.run(cr2._on_response(
        _FakeResp(crm.BASE + "/admin/xsd/xsdcjcx/qbcjcx")))
    check("教务主机 JSON 正常捕获", len(cr2._captured), 1)
    asyncio.run(cr2._on_response(_FakeResp("https://evil.com/x.json")))
    check("其他主机不捕获", len(cr2._captured), 1)
    asyncio.run(cr2._on_response(
        _FakeResp("https://" + crm.BASE.split("//", 1)[1] + ".evil.com/fake")))
    check("子串伪造域名不捕获", len(cr2._captured), 1)

    # ---------- 8) 环境变量 ----------
    log("\n=== 8) 环境变量生效 ===")
    a = fresh_app({"ZC_DEBUG_DIR": str(tmp / "envdir")}, tmp, idle=None)
    check("ZC_DEBUG_DIR 生效", a.DEBUG_DIR, str(tmp / "envdir"))
    check("启动即创建快照目录", (tmp / "envdir").exists(), True)
    check("默认空闲关闭 600s", a.IDLE_CLOSE_SECONDS, 600.0)
    check("默认无头（服务器友好）", a.IS_HEADLESS, True)
    a = fresh_app({}, tmp, idle="0")
    check("ZC_IDLE_CLOSE=0 生效", a.IDLE_CLOSE_SECONDS, 0.0)

    # ---------- 9) 清理端点 ----------
    log("\n=== 9) /api/debug/clean ===")
    a = fresh_app({}, tmp)
    (tmp / "crawl_error_x").mkdir(exist_ok=True)
    (tmp / "last_error.txt").write_text("x", encoding="utf-8")
    r = a.app.test_client().post("/api/debug/clean")
    check("清理端点返回 ok", r.get_json()["ok"], True)
    check("目录已清空", len(list(tmp.iterdir())), 0)

    # ---------- 10) 多租户鉴权 ZC_TOKENS ----------
    log("\n=== 10) ZC_TOKENS 多租户鉴权 ===")
    a = fresh_app({"ZC_TOKENS": "小明:tkAAAA,tkBBBB"}, tmp)
    c = a.app.test_client()
    check("令牌表 2 条", len(a.AUTH_MAP), 2)
    check("multiuser 打开", a.MULTIUSER, True)
    check("带标签令牌 → 用户 小明", a.AUTH_MAP.get("tkAAAA"), "小明")
    check("纯令牌 → 标签取前 6 位", a.AUTH_MAP.get("tkBBBB"), "tkBBBB")
    check("无 token → 401", c.get("/api/progress").status_code, 401)
    check("未知 token → 401",
          c.get("/api/progress", headers={"X-Token": "wrong"}).status_code, 401)
    check("令牌 A → 200",
          c.get("/api/progress", headers={"X-Token": "tkAAAA"}).status_code, 200)
    check("令牌 B → 200",
          c.get("/api/progress", headers={"X-Token": "tkBBBB"}).status_code, 200)
    d = c.get("/api/capabilities").get_json()
    check("capabilities.multiuser", d["multiuser"], True)
    check("capabilities.auth_required", d["auth_required"], True)
    hz = c.get("/healthz").get_json()
    check("healthz.multiuser", hz["multiuser"], True)

    # ---------- 11) 会话与快照目录隔离 ----------
    log("\n=== 11) 会话与快照隔离 ===")
    alice = a.manager.get_session("小明")
    check("A 的快照目录 = 根/小明", Path(alice.debug_dir) == tmp / "小明", True)
    check("B 的快照目录 = 根/tkBBBB",
          Path(a.manager.get_session("tkBBBB").debug_dir) == tmp / "tkBBBB", True)
    (Path(alice.debug_dir) / "snap1").mkdir(parents=True, exist_ok=True)
    (tmp / "tkBBBB" / "snap1").mkdir(parents=True, exist_ok=True)
    r = c.post("/api/debug/clean", headers={"X-Token": "tkAAAA"})
    check("A 清理返回 ok", r.get_json()["ok"], True)
    check("A 目录已清空", list(Path(alice.debug_dir).iterdir()), [])
    check("B 目录不受影响", (tmp / "tkBBBB" / "snap1").exists(), True)

    # ---------- 12) 忙占位按用户隔离 ----------
    log("\n=== 12) 忙占位按用户隔离 ===")
    check("A 占位成功", alice.try_begin("占位测试"), True)
    r = c.post("/api/crawl", json={}, headers={"X-Token": "tkAAAA"})
    check("A 忙时 A 抓取 → 409", r.status_code, 409)
    r = c.post("/api/crawl", json={}, headers={"X-Token": "tkBBBB"})
    check("A 忙时 B 抓取不 409（未登录 → 400）", r.status_code, 400)
    check("B 收到请先登录提示", "请先扫码登录" in r.get_json()["error"], True)
    alice.end()

    # ---------- 13) 并发引擎上限 & 空闲默认 ----------
    log("\n=== 13) ZC_MAX_ENGINES / 空闲默认 ===")
    a = fresh_app({"ZC_MAX_ENGINES": "3"}, tmp)
    check("ZC_MAX_ENGINES=3", a.MAX_ENGINES, 3)
    fresh_app({}, tmp)
    check("默认并发上限 2", a.MAX_ENGINES, 2)
    a = fresh_app({"ZC_TOKENS": "u:tokX"}, tmp, idle=None)
    check("多用户默认空闲 180s", a.IDLE_CLOSE_SECONDS, 180.0)
    a = fresh_app({}, tmp, idle=None)
    check("单用户默认空闲 600s", a.IDLE_CLOSE_SECONDS, 600.0)
    a = fresh_app({"ZC_TOKENS": "u:tokX", "ZC_IDLE_CLOSE": "300"}, tmp, idle=None)
    check("ZC_IDLE_CLOSE 覆盖默认", a.IDLE_CLOSE_SECONDS, 300.0)

    log("\n=== 结论：" + ("ALL TESTS PASSED" if not FAILS else f"{len(FAILS)} FAILED -> {FAILS}") + " ===")
    LOG.close()
    print("ALL TESTS PASSED" if not FAILS else f"FAILED: {FAILS}  (see _selftest_deploy_out.txt)")


main()