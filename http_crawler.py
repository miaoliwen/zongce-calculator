# -*- coding: utf-8 -*-
"""
综测计算器 · 纯 HTTP 成绩抓取引擎（低内存后端）
================================================
与 ``crawler.py``（Playwright + Chromium）对外接口一致，区别是全程只用
``requests`` 复现官方页面的 HTTP 流量，不启动浏览器：

  扫码：GET  cloudscanlogin 页面 → 解析 uuid/enc/二维码图片
        POST getauthstatus 轮询（与官方页面同参数、同节奏 3s/次）
        手机确认后 GET pcrefer（/admin/scanLogin）建立教务会话
  成绩：GET  qbcjcx 内容页 → 解析学年学期下拉与 jqGrid 取数地址
        POST jqGrid 接口（xnxq/xnxq2 + 标准 jqGrid 参数）→ 归一化成绩行

归一化、本机二次过滤、规则剔除直接复用 GradeCrawler 中经过自测的实现，
不重复造轮子。内存特征：常驻仅 Flask + requests（约 40~70 MB）。

环境变量：
  ZC_ENGINE  http（本引擎，默认）/ browser（Playwright 引擎）/ auto（同 http）
本引擎不支持「弹窗扫码」（那是本机可见浏览器兜底），需要时请改用 browser。
"""

import asyncio
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

# 复用 crawler 的常量、脱敏函数与 GradeCrawler 的纯逻辑（import 不启动浏览器）
from crawler import (
    BASE,
    GRADES_URL,
    GRADES_CONTENT_PATHS,
    SCAN_URL,
    PASSPORT,
    SEMESTER_ALL_VALUE,
    GRID_API_KEY,
    _scrub,
    GradeCrawler,
)

AUTHSTATUS_URL = PASSPORT + "/getauthstatus"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 官方页面：setInterval 3s × 50 次后显示二维码失效 → TTL 150s
QR_TTL_SECONDS = 150
POLL_INTERVAL = 3.0
PAGE_ROWS = 500

# 鲁棒性参数
DEFAULT_RETRIES = 2                 # 网络抖动/5xx 时额外尝试次数（共 3 次）
RETRY_BACKOFF = 0.6                # 退避基数：0.6s, 1.2s
RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_POLL_ERRORS = 5                # 轮询连续失败上限：超过才向用户报错
MIN_QR_BYTES = 50                  # 二维码图片最小合理体积

# 限流（HTTP 429）应对：退避远长于普通网络错误，且跨请求累积冷却，避免顶着
# 限流连续请求教务系统。冷却时长 30s→60s→120s 指数增长，任一请求成功即复位。
RATE_LIMIT_BACKOFF = 5.0           # 单请求内 429 退避基数：5s, 10s, …（上限 30s）
RATE_LIMIT_PACE_MAX = 10.0         # 冷却期内每个请求最多先等 10s（避免拖垮调用方超时）
RATE_LIMIT_COOLDOWN_MAX = 120.0    # 跨请求冷却时长上限


# --------------------------------------------------------------------------- #
# 轻量 HTML 解析（标准库正则，避免引入 bs4 依赖）
# --------------------------------------------------------------------------- #
def _attr(attrs, name):
    """从一段标签属性文本中取属性值（兼容单/双引号）。"""
    m = re.search(r'\b' + name + r'\s*=\s*(["\'])(.*?)\1', attrs, re.I | re.S)
    return m.group(2) if m else ""


def _input_value(html, field_id):
    """取 <input id=field_id ...> 的 value（兼容 value 在 id 前/后）。"""
    m = re.search(r'<input\b[^>]*\bid\s*=\s*["\']' + re.escape(field_id) + r'["\'][^>]*>',
                  html, re.I | re.S)
    if not m:
        return ""
    return _attr(m.group(0), "value")


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).strip()


def _parse_selects(html):
    """解析页面中全部 <select>：返回 [{id,name,label,options:[{value,text}]}]。"""
    items = []
    for sm in re.finditer(r"<select\b([^>]*)>(.*?)</select>", html, re.I | re.S):
        attrs, body = sm.group(1), sm.group(2)
        opts = []
        for om in re.finditer(r"<option\b([^>]*)>(.*?)</option>", body, re.I | re.S):
            opts.append({
                "value": _attr(om.group(1), "value"),
                "text": _strip_tags(om.group(2)),
            })
        before = html[:sm.start()]
        label = _strip_tags(before[-160:])[-80:]
        items.append({
            "id": _attr(attrs, "id"),
            "name": _attr(attrs, "name"),
            "label": label,
            "options": opts,
        })
    return items


def _find_grid_url(html):
    """
    从页面 JS 的 jqGrid 配置中解析取数地址：
      url: '/admin/xsd/xsdcjcx/xsdQueryXscjList?...'
      url: ctx + '/xsd/xsdcjcx/xsdQueryXscjList'
    优先返回含 xsdQueryXscjList 的；找不到返回 None。
    """
    patterns = [
        r"""url\s*:\s*["']([^"']+)["']""",
        r"""url\s*:\s*[A-Za-z_$][\w$.]*\s*\+\s*["']([^"']+)["']""",
    ]
    found = []
    for pat in patterns:
        found += re.findall(pat, html)
    for u in found:
        if GRID_API_KEY in u:
            return u
    return None


# --------------------------------------------------------------------------- #
# 筛选识别（与 GradeCrawler.discover_filters 的判定规则保持一致）
# --------------------------------------------------------------------------- #
def _is_semester_text(t):
    return bool(re.match(r"^\s*\d{4}\s*[-/]\s*\d{4}\s*[-/]\s*\d+\s*$", t or ""))


def _is_semester_select(it):
    opts = it["options"]
    if not opts:
        return False
    hit = sum(1 for o in opts if _is_semester_text(o["text"]))
    return hit >= max(1, len(opts) // 2)


def _is_year(it):
    if "学年" in (it["label"] or ""):
        return True
    return any(re.search(r"\d{4}\s*[-/]\s*\d{4}", o["text"]) for o in it["options"])


def _is_term(it):
    if "学期" in (it["label"] or ""):
        return True
    texts = [o["text"] for o in it["options"]]
    return len(texts) >= 2 and all(
        re.fullmatch(r"\s*([123]|[一二三四]|第?[一二三四123]学期?)\s*", t)
        for t in texts if t
    )


def _build_filters(selects, log):
    sem_items = [it for it in selects if _is_semester_select(it)]
    semester_pair = len(sem_items) >= 2
    if semester_pair:
        first = sem_items[0]
        second = next((it for it in sem_items if it is not first), sem_items[-1])
        start = next((it for it in sem_items if "起始" in (it["label"] or "")), first)
        end = next((it for it in sem_items if "终止" in (it["label"] or "")), second)
        if end is start:
            end = second if start is first else first
        year, term = start, end
    else:
        year = next((it for it in selects if _is_year(it)), None)
        term = next((it for it in selects if _is_term(it) and it is not year), None)

    def with_all(it):
        if it is None:
            return None
        has_all = any(
            ("全部" in o["text"] or "入学以来" in o["text"]
             or o["value"] == "" or o["value"] == SEMESTER_ALL_VALUE)
            for o in it["options"]
        )
        opts = list(it["options"])
        if not has_all:
            opts.insert(0, {"value": "__ALL__", "text": "全部"})
        return {**it, "options": opts}

    log(
        f"读取筛选条件：学年下拉 {'有' if year else '无'}，学期下拉 {'有' if term else '无'}"
        + ("（起止学年学期区间模式）" if semester_pair else "")
    )
    return {
        "year": with_all(year),
        "term": with_all(term),
        "semester_pair": semester_pair,
        "all_selects": selects,
    }


# --------------------------------------------------------------------------- #
# 纯 HTTP 引擎
# --------------------------------------------------------------------------- #
class HttpGradeCrawler:
    """接口与 GradeCrawler 对齐：app.py 可直接替换使用。"""

    def __init__(self, debug_dir=None, log=None, headless=True, progress=None):
        self._log_fn = log or (lambda msg: None)
        self._progress_fn = progress or (lambda *a: None)
        self.debug_dir = Path(debug_dir or "debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        # 借用 GradeCrawler 的纯逻辑（不 start、不启动浏览器）
        self._helper = GradeCrawler(
            debug_dir=str(self.debug_dir), log=self._log_fn, headless=True
        )

        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA})

        self.browser = None            # start() 后置位（app.py 据此判断引擎存活）
        self.qr_bytes = None
        self.last_excluded = []

        self._uuid = ""
        self._enc = ""
        self._pcrefer = ""
        self._qr_url = ""
        self._content_url = ""
        self._grid_url = ""
        self._started_at = 0.0
        self._last_html = ""
        self._poll_errors = 0
        self._rate_hits = 0           # 连续 429 计数（任一请求成功即复位）
        self._cooldown_until = 0.0    # 限流冷却截止时间戳
        self._closed = False

    # ---------------- 日志 ---------------- #
    def _log(self, msg):
        self._log_fn(msg)

    def _progress(self, stage, pct=None, detail=""):
        try:
            self._progress_fn(stage, pct, detail)
        except Exception:
            pass

    # ---------------- 统一请求封装（重试 / 限流冷却 / 编码） ---------------- #
    def _note_rate_limited(self):
        """命中限流：跨请求冷却按 30s→60s→120s 指数增长，成功后由 _request 复位。"""
        self._rate_hits += 1
        cooldown = min(RATE_LIMIT_COOLDOWN_MAX, 30.0 * (2 ** (self._rate_hits - 1)))
        self._cooldown_until = max(self._cooldown_until, time.time() + cooldown)
        self._log(f"教务系统限流，已进入冷却：后续 {int(cooldown)} 秒内主动放慢请求")

    def _request(self, method, url, *, retries=DEFAULT_RETRIES, timeout=20, **kw):
        """
        带有限重试的 HTTP 调用：
          - ConnectionError / Timeout / 5xx → 短退避（0.6s 起）后重试；
          - 429（限流）→ 长退避（5s 起）+ 跨请求冷却，重试耗尽给可读提示；
          - 显式按 UTF-8 解码（超星页面为 UTF-8，避免 requests 猜测成 ISO-8859-1）；
          - 其余状态码（含 4xx）原样返回，由调用方按业务判定。
        重试耗尽后抛 RuntimeError（信息可读，不含敏感内容）。
        """
        # 此前被限流过：先小步冷却再发，避免顶着 429 连续请求
        wait = self._cooldown_until - time.time()
        if wait > 0:
            self._log(f"限流冷却中，先等待 {min(int(wait) + 1, int(RATE_LIMIT_PACE_MAX))} 秒…")
            time.sleep(min(wait, RATE_LIMIT_PACE_MAX))
        last_exc = None
        for attempt in range(retries + 1):
            try:
                r = self.http.request(method, url, timeout=timeout, **kw)
                if r.status_code == 429:
                    self._note_rate_limited()
                    if attempt < retries:
                        delay = min(30.0, RATE_LIMIT_BACKOFF * (2 ** attempt))
                        self._log(
                            f"请求 {urlsplit(url).path} 被限流（429），"
                            f"{delay:.0f}s 后重试（{attempt + 1}/{retries}）"
                        )
                        time.sleep(delay)
                        continue
                    raise RuntimeError(
                        "教务系统返回限流（HTTP 429），请等待 1~2 分钟后再试；"
                        "若反复出现，请避开综测统计等高峰时段使用。"
                    )
                if r.status_code in RETRY_STATUS and attempt < retries:
                    delay = RETRY_BACKOFF * (2 ** attempt)
                    self._log(
                        f"请求 {urlsplit(url).path} 返回 {r.status_code}，"
                        f"{delay:.1f}s 后重试（{attempt + 1}/{retries}）"
                    )
                    time.sleep(delay)
                    continue
                if r.status_code < 400:
                    self._rate_hits = 0
                    self._cooldown_until = 0.0
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = "utf-8"
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                if attempt < retries:
                    delay = RETRY_BACKOFF * (2 ** attempt)
                    self._log(
                        f"网络异常（{e.__class__.__name__}），{delay:.1f}s 后重试"
                        f"（{attempt + 1}/{retries}）"
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    f"网络请求多次失败（{method} {urlsplit(url).path}，"
                    f"{e.__class__.__name__}）。请检查网络后重试。"
                )
        raise RuntimeError(f"请求失败：{last_exc}")

    def _json(self, method, url, *, retries=DEFAULT_RETRIES, timeout=20, **kw):
        """_request + JSON 解析；返回非 JSON 页面（风控/验证页）时给可读错误。

        超星接口（getauthstatus 等）实测用 text/html 承载 JSON——官方页 dataType:'json'
        也只解析响应体、不校验 content-type，因此先按内容解析，失败才报"非 JSON"。
        """
        r = self._request(method, url, retries=retries, timeout=timeout, **kw)
        try:
            return r.json()
        except ValueError:
            pass
        ct = r.headers.get("content-type", "")
        snippet = re.sub(r"\s+", " ", r.text[:120])
        raise RuntimeError(
            f"接口返回了非 JSON 内容（HTTP {r.status_code}，{ct or '无 content-type'}，{r.url}），"
            "可能触发了风控或登录验证。请稍后重试，或设置 ZC_ENGINE=browser。"
            + (f" 页面开头：{snippet}" if snippet else "")
        )

    # ---------------- 生命周期 ---------------- #
    async def start(self):
        # 同步网络 IO 放到线程，避免阻塞事件循环
        await asyncio.to_thread(self._warm)
        self.browser = object()

    def _warm(self):
        """访问一次扫码主机，确认网络可达并初始化会话（重试由 _request 负责）。"""
        r = self._request("GET", SCAN_URL, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(
                f"无法访问超星扫码服务（HTTP {r.status_code}）。"
                "请检查服务器网络/DNS。"
            )

    async def close(self):
        def _close():
            self._closed = True
            try:
                self.http.close()
            except Exception:
                pass
        await asyncio.to_thread(_close)
        self.browser = None

    async def health(self):
        """引擎是否仍可用（app.py 自愈逻辑调用）。"""
        return self.browser is not None and not self._closed

    async def logout(self):
        return await asyncio.to_thread(self._logout)

    def _logout(self):
        """清空会话 cookie 与登录后状态；引擎对象保留，下次扫码登录直接复用。"""
        try:
            self.http.close()
        except Exception:
            pass
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA})
        self._uuid = self._enc = self._pcrefer = ""
        self._qr_url = self._content_url = self._grid_url = ""
        self._last_html = ""
        self.qr_bytes = None
        self._started_at = 0.0
        self._poll_errors = 0
        self._log("已清除登录 cookie，可重新扫码登录")

    # ---------------- 扫码 ---------------- #
    async def open_scan_page(self):
        return await asyncio.to_thread(self._open_scan_page)

    def _open_scan_page(self):
        r = self._request("GET", SCAN_URL, timeout=20)
        html = r.text
        self._uuid = _input_value(html, "uuid")
        self._enc = _input_value(html, "enc")
        self._pcrefer = _input_value(html, "pcrefer") or (BASE + "/admin/scanLogin?")
        m = re.search(r'<img\b[^>]*\bid=["\']ewm["\'][^>]*>', html, re.I | re.S)
        qr_src = _attr(m.group(0), "src") if m else ""
        if not self._uuid or not qr_src:
            raise RuntimeError("二维码页面未返回 uuid/二维码地址")
        self._qr_url = urljoin(SCAN_URL, qr_src)
        img = self._request(
            "GET", self._qr_url,
            headers={"Referer": SCAN_URL, "Accept": "image/avif,image/webp,image/png,*/*"},
            timeout=20,
        )
        if img.status_code != 200 or "image" not in img.headers.get("content-type", ""):
            raise RuntimeError(
                f"二维码图片拉取失败（HTTP {img.status_code}）。"
                "可能当前网络/IP 被 passport2.chaoxing.com 限制。"
            )
        if len(img.content) < MIN_QR_BYTES:
            raise RuntimeError(
                "二维码图片内容异常（体积过小），可能被验证页拦截，请刷新重试。"
            )
        self.qr_bytes = img.content
        self._started_at = time.time()
        self._poll_errors = 0
        self._log("二维码已生成，等待手机扫码…")
        return {"uuid": self._uuid, "qr": self._qr_url}

    async def scan_state(self):
        return await asyncio.to_thread(self._scan_state)

    def _scan_state(self):
        if not self._uuid:
            return {"state": "idle"}
        if time.time() - self._started_at > QR_TTL_SECONDS:
            return {"state": "expired"}
        # 轮询请求本身容错：一次抖动不应让前端报错（官方页面也是静默重试）
        try:
            data = self._json(
                "POST", AUTHSTATUS_URL,
                data={"enc": self._enc, "uuid": self._uuid},
                retries=1, timeout=20,
            )
            self._poll_errors = 0
        except RuntimeError as e:
            self._poll_errors += 1
            if self._poll_errors >= MAX_POLL_ERRORS:
                return {"state": "error", "msg": str(e)}
            return {"state": "waiting"}
        if data.get("status"):
            if self._establish_session():
                self._log("扫码登录成功")
                return {"state": "confirmed"}
            return {"state": "waiting"}
        # type 实测可能是字符串（"4"），统一转数字比较
        try:
            t = float(data.get("type"))
        except (TypeError, ValueError):
            t = None
        if t == 4:
            return {"state": "scanned", "nick": data.get("nickname", "")}
        if t == 6:
            # 用户在手机上取消：官方页面直接 reload，这里返回等待态并刷新会话参数
            self._log("用户取消了扫码，已重置")
            self._open_scan_page()
            return {"state": "waiting"}
        if t == 2:
            # 服务端判定二维码已失效（实测 mes="二维码已失效"）
            return {"state": "expired"}
        return {"state": "waiting"}

    def _establish_session(self):
        """手机确认后访问 pcrefer 建立教务会话；教务侧可能有短暂延迟，做有限重试。"""
        for attempt in range(3):
            try:
                self._request("GET", self._pcrefer, allow_redirects=True,
                              retries=1, timeout=30)
                v = self._request("GET", GRADES_URL, allow_redirects=True,
                                  retries=1, timeout=30)
                if "/admin/login" not in v.url and v.status_code == 200:
                    return True
            except RuntimeError:
                pass
            if attempt < 2:
                time.sleep(1.0)
        return False

    async def refresh_scan(self):
        return await asyncio.to_thread(self._open_scan_page)

    # ---------------- 弹窗模式不支持 ---------------- #
    async def start_headed_window(self):
        raise RuntimeError(
            "纯 HTTP 引擎不支持「弹窗扫码」。请在服务端设置 ZC_ENGINE=browser "
            "使用 Playwright 引擎，或直接使用页内二维码。"
        )

    async def wait_headed_login(self, timeout=300):
        raise RuntimeError("纯 HTTP 引擎不支持弹窗模式，请使用页内二维码。")

    # ---------------- 读取筛选选项 ---------------- #
    async def discover_filters(self):
        return await asyncio.to_thread(self._discover_filters)

    def _open_content_page(self):
        """打开「全部成绩查询」内容页，返回 HTML。"""
        notes = []
        for path in GRADES_CONTENT_PATHS:
            try:
                r = self._request("GET", BASE + path, allow_redirects=True,
                                  timeout=30)
            except RuntimeError as e:
                notes.append(f"{path}: {e}")
                continue
            if "/admin/login" in r.url:
                raise RuntimeError("会话已失效，请重新扫码登录")
            if r.status_code == 200:
                self._content_url = r.url
                return r.text
            notes.append(f"{path}: HTTP {r.status_code}（最终 {r.url}）")
        raise RuntimeError("无法打开成绩查询页；" + "；".join(notes))

    def _discover_filters(self):
        html = self._open_content_page()
        self._last_html = html
        self._grid_url = _find_grid_url(html)
        selects = _parse_selects(html)
        return _build_filters(selects, self._log)

    # ---------------- 抓取成绩 ---------------- #
    async def crawl(self, year_value="__ALL__", term_value="__ALL__", filters=None):
        return await asyncio.to_thread(self._crawl, year_value, term_value, filters)

    def _resolve_grid_url(self, content_url, grid_path):
        """jqGrid 地址可能是绝对路径、相对路径或仅文件名，统一解析为绝对 URL。"""
        if grid_path.startswith("http"):
            return grid_path
        if grid_path.startswith("/"):
            return BASE + grid_path
        # 相对路径：以内容页所在目录为基准
        return urljoin(content_url, grid_path)

    def _query_grades(self, start_v, end_v):
        if not self._grid_url:
            raise RuntimeError(
                "未能从成绩页解析出 jqGrid 接口地址（页面可能改版），"
                "请设置 ZC_ENGINE=browser 使用浏览器引擎。"
            )
        url = self._resolve_grid_url(self._content_url, self._grid_url)
        raw_rows, page, total_pages = [], 1, 1
        while page <= total_pages:
            if page > 1:
                # 多页抓取放慢节奏：页间随机停 0.5~1s，不对教务系统连续快查
                time.sleep(random.uniform(0.5, 1.0))
            data = {
                "xnxq": start_v,
                "xnxq2": end_v,
                "_search": "false",
                "nd": str(int(time.time() * 1000)),
                "rows": str(PAGE_ROWS),
                "page": str(page),
                "sidx": "",
                "sord": "asc",
            }
            payload = self._json(
                "POST", url, data=data,
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Referer": self._content_url,
                         "Accept": "application/json, text/javascript, */*; q=0.01"},
                timeout=120,
            )
            if page == 1:
                try:
                    total_pages = max(1, int(payload.get("total", 1)))
                except Exception:
                    total_pages = 1
            rows = []
            self._helper._collect_rows(payload, rows, url)
            raw_rows.extend(rows)
            # 真实分页进度：首页之后即知总页数，映射到 8%–88% 区间
            self._progress(
                "抓取成绩",
                min(88.0, 8.0 + 80.0 * page / total_pages),
                f"第 {page}/{total_pages} 页 · 累计 {len(raw_rows)} 条",
            )
            if page >= total_pages or not rows:
                break
            page += 1
        return raw_rows

    def _crawl(self, year_value, term_value, filters=None):
        self._progress("准备查询", 2)
        if filters is None:
            filters = self._discover_filters()
        else:
            # 确保内容页与 jqGrid 地址已就绪
            if not self._grid_url:
                html = self._open_content_page()
                self._last_html = html
                self._grid_url = _find_grid_url(html)

        year_item, term_item = filters.get("year"), filters.get("term")
        if filters.get("semester_pair"):
            start_v, end_v = self._helper._semester_range(
                year_item, term_item, year_value, term_value
            )
            self._log(f"学年学期区间：起始 {start_v} → 终止 {end_v}")
        else:
            start_v = year_value if year_value != "__ALL__" else term_value
            end_v = term_value if term_value != "__ALL__" else start_v
            if start_v == "__ALL__":
                start_v = end_v = SEMESTER_ALL_VALUE

        self._progress("提交查询", 6, f"{start_v}~{end_v}")
        raw = self._query_grades(start_v, end_v)
        self._log(f"接口返回原始记录 {len(raw)} 条")

        rows = self._helper._merge(raw)
        self._log(f"合并去重后共 {len(rows)} 门课程")
        self._progress("解析合并", 90, f"共 {len(rows)} 门课程")
        if not rows:
            self.dump_sync("empty_result")
            raise RuntimeError(
                "未解析到任何成绩。已保存页面快照到 debug/ 目录，"
                "请确认所选学年学期已有成绩，或改用 ZC_ENGINE=browser。"
            )

        rows, dropped = self._helper._filter_semesters(rows, start_v, end_v)
        if not rows:
            self.dump_sync("empty_after_filter")
            raise RuntimeError(
                f"所选学年学期（{start_v}~{end_v}）下没有查到成绩。"
            )

        rows, excluded = self._helper._exclude_courses(rows)
        self.last_excluded = excluded
        self._log(
            f"最终 {len(rows)} 门纳入专业成绩"
            + (f"，另剔除 {len(excluded)} 门（不纳入专业成绩）" if excluded else "")
        )
        self._progress("完成", 100, f"{len(rows)} 门课程")
        rows.sort(key=lambda r: (r.get("year") or "", r.get("term") or "", r.get("code") or ""))
        return rows

    # ---------------- 诊断快照（HTTP 模式保存 HTML/JSON，无截图） ---------------- #
    async def dump(self, tag="error"):
        return await asyncio.to_thread(self.dump_sync, tag)

    def dump_sync(self, tag="error"):
        ts = time.strftime("%Y%m%d_%H%M%S")
        d = self.debug_dir / f"{tag}_{ts}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            if self._last_html:
                (d / "content.html").write_text(_scrub(self._last_html), encoding="utf-8")
            meta = {
                "content_url": self._content_url,
                "grid_url": self._grid_url,
                "qr_url": self._qr_url,
            }
            (d / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            (d / "dump_error.txt").write_text(str(e), encoding="utf-8")
        self._helper._prune_debug()
        self._log(f"诊断快照已保存（已脱敏）：{d}")
        return str(d)
#（注：内容由AI生成）
