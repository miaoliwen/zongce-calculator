# -*- coding: utf-8 -*-
"""
综测计算机 · 本地 / 云服务器服务（含多人共用「形态 B」支持）
================================================
启动（本机开发）：
    python app.py              # http://127.0.0.1:8765
启动（生产，见《部署方案.md》）：
    gunicorn -w 1 --threads 8 --timeout 360 -b 127.0.0.1:8765 app:app

为什么需要一个本地小服务：
  浏览器同源策略不允许“综测计算机”页面直接跨域请求 passport2.chaoxing.com
  的二维码轮询接口和教务系统成绩接口。本地服务负责：
    1. 在后台浏览器（Playwright）中打开官方二维码页并维持轮询；
    2. 把二维码图片、扫码状态转给前端；
    3. 登录成功后读取筛选选项、执行成绩爬取并返回归一化 JSON。
  服务只监听本机（127.0.0.1），不上传任何账号信息。

部署注意（安全模型随环境变化，务必按环境设置）：
  ZC_ALLOWED_ORIGINS  跨域白名单，逗号分隔。**未设置**时兼容旧用法（只放行 file:// 的
                      Origin: null）；显式设为空字符串 = 拒绝一切跨域（云服务器同源部署
                      就该这样）。绝不要再用 "*"。
  ZC_TOKEN            单用户访问令牌（形态 A）。设置后所有 /api/* 必须带 X-Token 头
                      （纵深防御，第一道闸建议放在反向代理的 Basic Auth）。
  ZC_TOKENS           多租户令牌表（形态 B，多人共用）：逗号分隔的「标签:令牌」或纯令牌，
                      如 `张三:tkAAA,李四:tkBBB`（省略标签时用令牌前 6 位当标签）。
                      设置后每人独立浏览器会话、日志与快照目录，互不可见。
  ZC_MAX_ENGINES      全局并发引擎上限（默认 2）：同时最多 N 个重型操作（登录/抓取）
                      在跑，其余排队（asyncio.Semaphore）；新建引擎超出上限时
                      优先回收最久未用的空闲浏览器，防小内存机器 OOM。
  ZC_DEBUG_WRITE      1=允许落盘诊断快照（默认）/ 0=完全不落盘（多人共用建议 0）。
  ZC_HEADLESS        1=无头（云服务器，默认）/ 0=允许弹出可见窗口（本机）。
  ZC_DEBUG_DIR       诊断快照目录（默认 ./debug）。
  ZC_DEBUG_KEEP      仅保留最近 N 个快照（默认 5，0=不清理）。
  ZC_IDLE_CLOSE      空闲多少秒后自动关闭浏览器释放内存（默认 600，0=不关）。
"""

import asyncio
import os
import re
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, g, jsonify, request, send_file, send_from_directory

from crawler import GradeCrawler

app = Flask(__name__, static_folder="static", static_url_path="")

# --------------------------------------------------------------------------- #
# 环境配置（云服务器上只改环境变量，不改代码）
# 这里自行解析而不引用 crawler 的模块级常量：避免受模块导入顺序影响，
# 也让“配置读一次”这件事发生在本文件、清晰可控。
# --------------------------------------------------------------------------- #
DEBUG_DIR = os.environ.get("ZC_DEBUG_DIR", "debug")
IS_HEADLESS = os.environ.get("ZC_HEADLESS", "1").strip().lower() not in ("0", "false", "no")

# 跨域白名单：未显式配置时只放行 file:// 场景，避免任意网站读取本机成绩
_ORIGINS_ENV = os.environ.get("ZC_ALLOWED_ORIGINS")
if _ORIGINS_ENV is None:
    ALLOWED_ORIGINS = {"null"}                      # file:// 页面（本机双击 HTML 的旧用法）
else:
    ALLOWED_ORIGINS = {o.strip() for o in _ORIGINS_ENV.split(",") if o.strip()}


def _env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


# 启动即确保快照目录存在且可写：部署时宁可启动失败，也别等到出错才暴露权限问题
try:
    Path(DEBUG_DIR).mkdir(parents=True, exist_ok=True)
except Exception as _e:      # noqa: BLE001
    print(f"警告：快照目录不可用（{DEBUG_DIR}）：{_e}", flush=True)


# --------------------------------------------------------------------------- #
# 多租户令牌（形态 B）
# --------------------------------------------------------------------------- #
def _sanitize_uid(s):
    """标签 → 可安全作目录名的用户 ID（保留字母数字/CJK/连字符，限长 24）。"""
    s = re.sub(r"[^\w-]", "_", s, flags=re.UNICODE).strip("_")[:24]
    return s or "user"


def _parse_tokens(raw):
    """`张三:tkAAA,tkBBB` → {tkAAA: '张三', tkBBB: 'tkBBB'}（标签重复自动加后缀）。"""
    mapping, used = {}, {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            label, _, tok = part.partition(":")
            label, tok = label.strip(), tok.strip()
        else:
            label, tok = "", part
        if not tok:
            continue
        if not label:
            label = tok[:6]
        label = _sanitize_uid(label)
        n = used.get(label, 0) + 1
        used[label] = n
        if n > 1:
            label = f"{label}-{n}"
        mapping[tok] = label
    return mapping


_TOKENS_ENV = os.environ.get("ZC_TOKENS", "").strip()
_SINGLE_TOKEN = os.environ.get("ZC_TOKEN", "").strip()
if _TOKENS_ENV:
    AUTH_MAP = _parse_tokens(_TOKENS_ENV)           # 令牌 → 用户 ID
    MULTIUSER = True
elif _SINGLE_TOKEN:
    AUTH_MAP = {_SINGLE_TOKEN: "default"}
    MULTIUSER = False
else:
    AUTH_MAP = {}
    MULTIUSER = False

MAX_ENGINES = max(1, _env_int("ZC_MAX_ENGINES", 2))

# 空闲回收阈值：多人共用时更激进（默认 180s，尽快还内存），单用户 600s；
# ZC_IDLE_CLOSE 始终可显式覆盖。
_DEFAULT_IDLE = "180" if MULTIUSER else "600"
IDLE_CLOSE_SECONDS = _env_float("ZC_IDLE_CLOSE", _DEFAULT_IDLE)


# --------------------------------------------------------------------------- #
# 后台 asyncio 线程：Playwright 对象必须始终在同一个事件循环上使用
# --------------------------------------------------------------------------- #
class Session:
    """
    一个用户的一整套状态：独立浏览器引擎、串行锁、日志、忙占位。
    用户之间互不共享任何引擎状态；同用户的多请求在 _serial 上排队，
    重量级操作用 _heavy 非阻塞占位（已在跑则 409）。
    """

    def __init__(self, uid, debug_dir):
        self.uid = uid
        self.debug_dir = debug_dir
        self.engine = None
        self.filters = None
        self.logs = []
        self._serial = asyncio.Lock()      # 协程级串行（引擎对象绝不交错操作）
        self._heavy = threading.Lock()     # Flask 线程侧的任务占位
        self.busy_label = None
        self.busy_since = 0.0
        self.last_used = time.time()

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]
        print(f"[{self.uid}] {line}", flush=True)

    def try_begin(self, label):
        if not self._heavy.acquire(blocking=False):
            return False
        self.busy_label = label
        self.busy_since = time.time()
        return True

    def end(self):
        self.busy_label = None
        self.last_used = time.time()
        try:
            self._heavy.release()
        except RuntimeError:
            pass

    def busy_message(self):
        if not self.busy_label:
            return ""
        return (
            f"你已有任务在进行中（{self.busy_label}，已运行 "
            f"{int(time.time() - self.busy_since)} 秒），请稍后再试"
        )

    def engine_info(self):
        eng = self.engine
        return {
            "started": eng is not None and eng.browser is not None,
            "has_qr": bool(eng and eng.qr_bytes),
        }


class Manager:
    """
    全体用户会话的管理者（替代旧版单用户 AsyncRunner）：
      - 每用户一个 Session（独立引擎，互不可见）；
      - 重型操作经 run_heavy()：同用户串行 + 全局 Semaphore(ZC_MAX_ENGINES) 限并发；
      - 新建引擎超出上限时先回收最久未用的空闲浏览器（LRU 淘汰）；
      - 空闲超过 ZC_IDLE_CLOSE 秒的浏览器自动关闭释放内存。
    """

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.sessions = {}
        self._map = threading.Lock()
        self.logs = []
        self._slots = None       # 首次在 loop 内使用时创建，避免绑定到错误事件循环
        if IDLE_CLOSE_SECONDS > 0:
            threading.Thread(target=self._idle_watch, daemon=True).start()

    # ---------------- 会话与日志 ---------------- #
    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]
        print(line, flush=True)

    def session(self, uid):
        with self._map:
            sess = self.sessions.get(uid)
            if sess is None:
                # default 用户沿用根快照目录（兼容本机单用户旧用法），其余按用户隔离
                ddir = DEBUG_DIR if uid == "default" else str(Path(DEBUG_DIR) / uid)
                sess = Session(uid, ddir)
                self.sessions[uid] = sess
            sess.last_used = time.time()
            return sess

    def get_session(self, uid):
        with self._map:
            return self.sessions.get(uid)

    def current(self):
        return self.session(getattr(g, "uid", "default"))

    # ---------------- 调用入口 ---------------- #
    def run(self, sess, coro, timeout=300):
        """同用户串行执行协程（轻量操作也走这里，避免与重型操作交错）。"""
        sess.last_used = time.time()

        async def _serialized():
            async with sess._serial:
                return await coro

        return asyncio.run_coroutine_threadsafe(_serialized(), self.loop).result(timeout)

    def run_heavy(self, sess, coro, timeout=300):
        """重型操作：同用户串行 + 全局并发上限（跨用户排队，而不是顶爆内存）。"""
        sess.last_used = time.time()

        async def _wrapped():
            async with sess._serial, await self._get_slots():
                return await coro

        return asyncio.run_coroutine_threadsafe(_wrapped(), self.loop).result(timeout)

    async def _get_slots(self):
        if self._slots is None:
            self._slots = asyncio.Semaphore(MAX_ENGINES)
        return self._slots

    # ---------------- 引擎生命周期 ---------------- #
    def _live_engines(self):
        with self._map:
            return sum(
                1 for s in self.sessions.values()
                if s.engine is not None and s.engine.browser is not None
            )

    def _lru_idle_session(self, exclude):
        with self._map:
            cands = [
                s for s in self.sessions.values()
                if s.uid != exclude and s.engine is not None
                and s.engine.browser is not None and not s._heavy.locked()
            ]
            return min(cands, key=lambda s: s.last_used) if cands else None

    async def ensure_engine(self, sess):
        """取到该用户的引擎，必要时创建；总引擎数超上限先回收最久未用的空闲浏览器。

        MAX_ENGINES 是硬性内存上限：正常情况下重型操作都持有全局 slot，
        必有空闲引擎可回收；万一全部被占（竞态窗口），短暂等待而不是放行，
        绝不让存活浏览器数突破上限。
        """
        if sess.engine is not None and sess.engine.browser is not None:
            return sess.engine
        deadline = time.time() + 30
        while self._live_engines() >= MAX_ENGINES:
            victim = self._lru_idle_session(exclude=sess.uid)
            if victim is not None:
                self.log(f"引擎数达上限（{MAX_ENGINES}），回收用户 {victim.uid} 的闲置引擎")
                try:
                    await victim.engine.close()
                except Exception:
                    pass
                victim.engine = None
                victim.filters = None
                continue
            if time.time() >= deadline:
                raise RuntimeError("浏览器引擎数已达上限且均被占用，请稍后再试")
            await asyncio.sleep(1)
        eng = GradeCrawler(debug_dir=sess.debug_dir, log=sess.log, headless=IS_HEADLESS)
        await eng.start()
        sess.engine = eng
        return eng

    def drop_engine(self, sess):
        sess.engine = None
        sess.filters = None

    # ---------------- 空闲自动关闭 ---------------- #
    def _idle_watch(self):
        while True:
            time.sleep(60)      # 惰性回收：最多晚 60s，换来更低的常驻唤醒频率
            try:
                with self._map:
                    targets = [
                        s for s in self.sessions.values()
                        if s.engine is not None and not s._heavy.locked()
                        and time.time() - s.last_used >= IDLE_CLOSE_SECONDS
                    ]
                for sess in targets:
                    self.log(
                        f"用户 {sess.uid} 空闲 {int(IDLE_CLOSE_SECONDS)} 秒，"
                        "自动关闭浏览器释放内存"
                    )
                    self.run(sess, sess.engine.close(), timeout=60)
                    self.drop_engine(sess)
            except Exception as exc:      # noqa: BLE001
                self.log(f"空闲关闭出错（忽略）：{exc}")


manager = Manager()


# --------------------------------------------------------------------------- #
# 鉴权：令牌 → 用户
# --------------------------------------------------------------------------- #
# <img src> 无法自定义请求头，二维码图片额外允许用 query 传令牌
IMG_TOKEN_PATHS = ("/api/scan/qr.png",)

# 能力探测必须公开：前端正靠它得知“本服务需要令牌”，否则用户只会看到 401 而不知所以。
# 该端点不返回任何隐私信息，仅环境标志。
PUBLIC_API_PATHS = ("/api/capabilities",)


def _presented_token():
    given = request.headers.get("X-Token") or ""
    if not given and request.path in IMG_TOKEN_PATHS:
        given = request.args.get("token", "")
    return given


@app.before_request
def require_token():
    """
    鉴权并确定当前用户：
      - 配置了 ZC_TOKENS：令牌必须命中令牌表，命中后 g.uid = 该令牌的标签（每人独立会话）；
      - 仅配置 ZC_TOKEN：令牌命中即视为 default 用户（形态 A）；
      - 都没配置：不鉴权，一律 default（本机单用户旧用法）。
    """
    g.uid = "default"
    if not AUTH_MAP or request.method == "OPTIONS":
        return None
    if not request.path.startswith("/api/"):
        return None
    if request.path in PUBLIC_API_PATHS:
        return None
    uid = AUTH_MAP.get(_presented_token())
    if uid is None:
        return jsonify({"ok": False, "error": "未授权：缺少或错误的 X-Token"}), 401
    g.uid = uid
    return None


@app.after_request
def add_cors(resp):
    """
    跨域：只回显白名单内的 Origin，不再使用 "*"。
    本机双击 HTML（file://）时 Origin 为字符串 "null"，默认放行；
    云服务器同源部署时设置 ZC_ALLOWED_ORIGINS= （空值）即可关闭全部跨域。
    """
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Token"
        resp.headers["Vary"] = "Origin"
    return resp


def error(e, status=500):
    tb = traceback.format_exc()
    print(tb, flush=True)
    try:
        Path(DEBUG_DIR).mkdir(parents=True, exist_ok=True)
        with open(Path(DEBUG_DIR) / "last_error.txt", "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{tb}")
    except Exception:
        pass
    return jsonify({"ok": False, "error": str(e)}), status


def begin_or_409(label):
    """重量级任务入口守卫（按用户占位）：返回 (session, None) 或 (None, 409响应)。"""
    sess = manager.current()
    if sess.try_begin(label):
        return sess, None
    return None, (jsonify({"ok": False, "error": sess.busy_message()}), 409)


def require_engine(sess):
    """需要已登录的引擎；没有则返回可读错误，而不是留下难懂的 500。"""
    if sess.engine is None or sess.engine.browser is None:
        return jsonify({"ok": False, "error": "请先扫码登录教务系统"}), 400
    return None


# --------------------------------------------------------------------------- #
# 页面 & 探活
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/healthz")
def healthz():
    """探活：systemd / 反向代理 / 监控用。不暴露任何用户或隐私信息。"""
    busy = [s.uid for s in manager.sessions.values() if s.busy_label]
    return jsonify({
        "ok": True,
        "users": len(manager.sessions),
        "engines": manager._live_engines(),
        "busy": busy,
        "headless": IS_HEADLESS,
        "auth": bool(AUTH_MAP),
        "multiuser": MULTIUSER,
    })


@app.route("/api/capabilities")
def capabilities():
    """
    告诉前端当前环境能力：无头（云服务器）时要隐藏「改用弹窗扫码」，
    因为服务器没有显示，弹出可见窗口必然失败。
    multiuser=True 时前端展示合规声明（仅限查询本人成绩）后才允许使用。
    """
    return jsonify({
        "ok": True,
        "headed": not IS_HEADLESS,
        "auth_required": bool(AUTH_MAP),
        "multiuser": MULTIUSER,
        "busy": manager.current().busy_label,
    })


# --------------------------------------------------------------------------- #
# 扫码登录（二维码内嵌模式）
# --------------------------------------------------------------------------- #
@app.route("/api/scan/start", methods=["POST"])
def scan_start():
    """启动后台浏览器并打开二维码页。mode=window 仅本机可用（服务器无显示）。"""
    mode = (request.json or {}).get("mode", "inline")
    if mode == "window" and IS_HEADLESS:
        return jsonify({
            "ok": False,
            "error": "当前为无头模式（云服务器无显示），无法弹出浏览器窗口，请使用页内二维码。",
        }), 400
    sess, resp = begin_or_409("扫码登录")
    if resp:
        return resp

    async def _job():
        eng = await manager.ensure_engine(sess)
        if mode == "window":
            await eng.start_headed_window()
            return {"mode": "window"}
        await eng.open_scan_page()
        return {"mode": "inline"}

    try:
        r = manager.run_heavy(sess, _job(), timeout=120)
        return jsonify({"ok": True, **r})
    except Exception as e:
        return error(e)
    finally:
        sess.end()


@app.route("/api/scan/qr.png")
def scan_qr():
    eng = manager.current().engine
    if eng is None or not eng.qr_bytes:
        return jsonify({"ok": False, "error": "二维码尚未生成"}), 404
    return send_file(
        __import__("io").BytesIO(eng.qr_bytes),
        mimetype="image/png",
        download_name="qr.png",
    )


@app.route("/api/scan/state")
def scan_state():
    sess = manager.current()
    if sess.engine is None:
        return jsonify({"state": "idle"})
    try:
        return jsonify(manager.run(sess, sess.engine.scan_state(), timeout=30))
    except Exception as e:
        return error(e)


@app.route("/api/scan/refresh", methods=["POST"])
def scan_refresh():
    sess = manager.current()
    resp = require_engine(sess)
    if resp:
        return resp
    try:
        manager.run(sess, sess.engine.refresh_scan(), timeout=60)
        return jsonify({"ok": True})
    except Exception as e:
        return error(e)


@app.route("/api/scan/wait", methods=["POST"])
def scan_wait():
    """窗口模式：阻塞等待用户在弹出的浏览器里完成扫码。"""
    sess, resp = begin_or_409("窗口扫码等待")
    if resp:
        return resp
    resp = require_engine(sess)
    if resp:
        sess.end()
        return resp
    try:
        ok = manager.run_heavy(sess, sess.engine.wait_headed_login(), timeout=320)
        return jsonify({"ok": ok, "state": "confirmed" if ok else "timeout"})
    except Exception as e:
        return error(e)
    finally:
        sess.end()


# --------------------------------------------------------------------------- #
# 筛选选项 & 爬取
# --------------------------------------------------------------------------- #
@app.route("/api/options", methods=["POST"])
def options():
    sess, resp = begin_or_409("读取学年学期")
    if resp:
        return resp
    resp = require_engine(sess)
    if resp:
        sess.end()
        return resp
    try:
        filters = manager.run_heavy(sess, sess.engine.discover_filters(), timeout=120)
        sess.filters = filters
        return jsonify({"ok": True, "filters": filters})
    except Exception as e:
        return error(e)
    finally:
        sess.end()


@app.route("/api/crawl", methods=["POST"])
def crawl():
    body = request.json or {}
    year_value = body.get("year", "__ALL__")
    term_value = body.get("term", "__ALL__")
    sess, resp = begin_or_409("抓取成绩")
    if resp:
        return resp
    resp = require_engine(sess)
    if resp:
        sess.end()
        return resp
    try:
        rows = manager.run_heavy(
            sess,
            sess.engine.crawl(year_value, term_value, sess.filters),
            timeout=300,
        )
        return jsonify({
            "ok": True,
            "rows": rows,
            "excluded": getattr(sess.engine, "last_excluded", []),
            "query": {"year": year_value, "term": term_value},
        })
    except Exception as e:
        # 出错时尽量留下诊断快照
        try:
            manager.run(sess, sess.engine.dump("crawl_error"), timeout=60)
        except Exception:
            pass
        return error(e)
    finally:
        sess.end()


@app.route("/api/progress")
def progress():
    return jsonify({"logs": manager.current().logs[-50:]})


@app.route("/api/dump", methods=["POST"])
def dump():
    sess, resp = begin_or_409("生成快照")
    if resp:
        return resp
    resp = require_engine(sess)
    if resp:
        sess.end()
        return resp
    try:
        path = manager.run_heavy(sess, sess.engine.dump("manual"), timeout=60)
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return error(e)
    finally:
        sess.end()


@app.route("/api/close", methods=["POST"])
def close():
    sess = manager.current()
    try:
        if sess.engine is not None:
            manager.run(sess, sess.engine.close(), timeout=30)
        manager.drop_engine(sess)
        return jsonify({"ok": True})
    except Exception as e:
        return error(e)


@app.route("/api/debug/clean", methods=["POST"])
def debug_clean():
    """清空**本人**的诊断快照：快照含成绩截图与页面 HTML，不要长期留在服务器上。"""
    sess, resp = begin_or_409("清理快照")
    if resp:
        return resp
    try:
        import shutil
        removed = 0
        root = Path(sess.debug_dir)
        if root.exists():
            for p in root.iterdir():
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                    removed += 1
                except Exception:
                    pass
        sess.log(f"已清理诊断快照 {removed} 项")
        return jsonify({"ok": True, "removed": removed})
    except Exception as e:
        return error(e)
    finally:
        sess.end()


if __name__ == "__main__":
    # 默认只监听本机。要对外提供服务请用 gunicorn + 反向代理（见《部署方案.md》），
    # 而不是把 host 改成 0.0.0.0 直接暴露。
    host = os.environ.get("ZC_HOST", "127.0.0.1")
    port = int(os.environ.get("ZC_PORT", "8765"))
    if host not in ("127.0.0.1", "localhost"):
        print(
            "!! 警告：正在对外监听。请确认已设置 ZC_TOKEN/ZC_TOKENS 并置于反向代理"
            "（HTTPS + 鉴权）之后，否则同网络内任何人都能读取你的成绩。",
            flush=True,
        )
    if not AUTH_MAP:
        print("提示：未设置 ZC_TOKEN/ZC_TOKENS（仅靠反向代理鉴权时属正常）。", flush=True)
    if MULTIUSER:
        print(
            f"多人共用模式：{len(AUTH_MAP)} 个独立令牌，"
            f"并发引擎上限 {MAX_ENGINES}，各用户快照目录按 {DEBUG_DIR}/<用户> 隔离。",
            flush=True,
        )
    if not IS_HEADLESS:
        print("提示：ZC_HEADLESS=0，已启用可弹出窗口模式（适合本机）。", flush=True)
    print(f"服务启动：http://{host}:{port}   快照目录：{DEBUG_DIR}", flush=True)
    app.run(host=host, port=port, threaded=True, debug=False)
#（注：内容由AI生成）
