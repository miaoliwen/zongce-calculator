# -*- coding: utf-8 -*-
"""
综测计算机 · 教务系统成绩爬取引擎
==================================
目标系统：超星教务管理系统（南通师范高等专科学校）
  登录页： https://ntsf.jw.chaoxing.com/admin/login
  成绩页： https://ntsf.jw.chaoxing.com/admin/indexMain/M1402 （信息查询 → 全部成绩查询）

设计要点（为什么这样写能“完整爬取”）：
1. 只走扫码登录。教务系统登录页把二维码放在 iframe 中：
       https://passport2.chaoxing.com/cloudscanlogin?pcrefer=.../admin/scanLogin?
   二维码页面内部每 3 秒 POST /getauthstatus 轮询，手机（学习通 App）确认后
   页面顶层跳转到 pcrefer（/admin/scanLogin），教务会话随即建立。
   本引擎在后台浏览器上下文里打开这个二维码页面让它自己轮询，
   再把二维码图片取出来展示给“综测计算机”页面，无需逆向加密、无需账密。

2. 成绩抓取采用“双保险”：
   - XHR 拦截：点击查询后，截获所有 JSON 响应，递归识别其中的成绩行（原始结构数据，最全）；
   - DOM 解析：解析页面渲染出的成绩表格，并自动翻页（含 layui 分页）。
   两路结果按 课程号/课程名+学年学期 合并去重、互补缺失字段。

3. 选择器自适应：成绩页可能位于 iframe 中，引擎遍历全部 frame 按关键词打分定位；
   学年/学期下拉先“读出真实选项”再按选项值精确选择，避免硬编码失效。
   任何一步失败都会把截图、各 frame HTML、截获的 JSON 落到 debug/ 目录，便于排查。

注意：本文件只负责“登录 + 抓取 + 归一化”，不绑定任何综测计分规则；
     归一化后的成绩行交给前端（或你的业务代码）去算综测分。
"""

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote, unquote_plus, urlsplit

from playwright.async_api import async_playwright

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
BASE = os.environ.get("ZC_BASE_URL", "https://ntsf.jw.chaoxing.com")
BASE_HOST = urlsplit(BASE).netloc      # 精确主机名：防止 chaoxing.com.evil.com 前缀伪造
LOGIN_URL = f"{BASE}/admin/login"
GRADES_URL = f"{BASE}/admin/indexMain/M1402"
# 菜单「全部成绩查询」的真实内容页路径（左侧菜单 openTabForMain 的地址原样保留 /admin// 双斜杠）
GRADES_CONTENT_PATHS = ["/admin//xsd/xsdcjcx/qbcjcx", "/admin/xsd/xsdcjcx/qbcjcx"]
GRADES_MENU_TEXT = "全部成绩查询"
PASSPORT = os.environ.get("ZC_PASSPORT_URL", "https://passport2.chaoxing.com")
# 登录页 iframe 实际使用的二维码地址（pcrefer 必须与教务系统回调一致）
SCAN_URL = (
    f"{PASSPORT}/cloudscanlogin?pcrefer="
    + quote(f"{BASE}/admin/scanLogin?", safe="")
    + "&customurl=&mobiletip="
    + quote("教务管理系统")
)

# 系统 Chromium/Edge 兜底候选：仅在 Playwright 自带浏览器缺失时使用
# （典型场景：打包成 exe 分发，用户机器上没执行过 `playwright install`）
CHROMIUM_CANDIDATES = [
    os.environ.get("JW_CHROMIUM", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/local/bin/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]

# 成绩字段的中文/接口键名 → 归一化字段
FIELD_ALIASES = {
    "code":   ["kch", "kcdm", "kcbh", "课程号", "课程代码", "课程编号", "coursecode", "courseid"],
    # 「课程/环节」列把课程号+课程名合并在一起（如 “[CS1001]程序设计基础”），归一化时再拆开
    "name":   ["kcmc", "课程名称", "课程名", "课程/环节", "课程环节", "coursename", "name", "kc"],
    # 课程学分（应得）与 获得学分（实得）分开映射，_normalize_row 里 课程学分 优先
    "credit": ["xf", "kcxf", "zxf", "学分", "课程学分", "credit", "coursecredit", "totalperiod"],
    "credit_earned": ["hdxf", "hdxxf", "获得学分", "已获学分"],
    # 「全部成绩查询」页的成绩列是“综合成绩 / 分项成绩”，两者都要认
    "score":  ["cj", "zcj", "zhcj", "zpcj", "zpcj", "成绩", "综合成绩", "总评成绩", "分数", "score", "grade", "cjms"],
    "score_item": ["fxcj", "bfcj", "分项成绩"],
    "nature": ["kcxz", "kclb", "kcsx", "课程性质", "课程属性", "课程类别", "nature", "type"],
    "gpa":    ["jd", "gpa", "绩点", "point"],
    "teacher":["jsmc", "js", "教师", "任课教师", "teacher"],
    "year":   ["xn", "xnxq", "学年学期", "学年", "schoolyear", "academicyear"],
    "term":   ["xq", "xqm", "学期", "term", "semester"],
    "classname": ["jxbmc", "教学班", "班级"],
    "retake": ["cxbs", "cxbj", "重修", "补考"],
}

# 「全部成绩查询」页的按钮文案是“搜索”，优先匹配；其余用于兼容别的页面
QUERY_BUTTON_TEXTS = ["搜索", "查询", "成绩查询", "检索", "确定"]

# 学年学期下拉里“入学以来”的取值（等同全部学期），页面 ready 时默认就是它
SEMESTER_ALL_VALUE = "001"

# jqGrid 取数接口路径片段（用于从截获的请求里核对筛选参数）
GRID_API_KEY = "xsdQueryXscjList"

# 不纳入「专业学习成绩」的课程（按课程名称包含匹配）。
# 综测规则里体育不计入专业成绩加权，故默认剔除，避免拉低综测分。
EXCLUDE_COURSE_KEYWORDS = ["体育"]

# --------------------------------------------------------------------------- #
# 部署相关：可用环境变量覆盖（云服务器上无需改代码）
#   ZC_DEBUG_DIR  诊断快照目录，默认 ./debug
#   ZC_HEADLESS   1=无头（服务器，默认） / 0=可弹出可见窗口（本机）
#   ZC_DEBUG_KEEP 仅保留最近 N 个快照目录，默认 5；0 表示不清理
# --------------------------------------------------------------------------- #
DEFAULT_DEBUG_DIR = os.environ.get("ZC_DEBUG_DIR", "debug")
DEFAULT_HEADLESS = os.environ.get("ZC_HEADLESS", "1").strip() not in ("0", "false", "False")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


DEBUG_KEEP = _env_int("ZC_DEBUG_KEEP", 5)
# 多人共用时建议 0：完全不落盘，杜绝任何人通过快照看到他人成绩
DEBUG_WRITE = _env_int("ZC_DEBUG_WRITE", 1)

# 快照里需要打码的敏感字段（响应里带着学号/姓名/部门标识）
SENSITIVE_JSON_KEYS = (
    "currentUserName", "currentUserId", "currentDepartmentId", "userRoleId",
    "xhid", "xh", "sfzh", "mobile", "phone", "email",
)


def _scrub(text):
    """
    对快照文本做脱敏：JSON 里的学号/姓名等字段值、11 位手机号。
    只改文本值、不动结构，适配选择器仍然可用，但可以放心把快照发给他人排查。
    """
    if text in (None, ""):
        return text
    out = str(text)
    for key in SENSITIVE_JSON_KEYS:
        out = re.sub(rf'("{key}"\s*:\s*)"[^"]*"', r'\1"***"', out)
        out = re.sub(rf"(value=)(\"?)[^\s\"'>]*(\"?)([^>]*name=\"{key}\")", r"\1\2***\3\4", out)
    out = re.sub(r"\b1[3-9]\d{9}\b", "1**********", out)
    return out


def _norm_key(k):
    return re.sub(r"[\s_\-]", "", str(k)).lower()


def _map_field(key):
    nk = _norm_key(key)
    for canon, aliases in FIELD_ALIASES.items():
        for a in aliases:
            if nk == _norm_key(a):
                return canon
    return None


def _safe_post_data(response):
    """取请求体（POST 参数），任何异常都不影响抓取。"""
    try:
        return response.request.post_data or ""
    except Exception:
        return ""


def _to_float(s):
    """从 '3.5学分'、'85'、'优秀' 之类中取浮点数，取不到返回 None。"""
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else None


# --------------------------------------------------------------------------- #
# 核心引擎
# --------------------------------------------------------------------------- #
class GradeCrawler:
    def __init__(self, debug_dir=None, headless=None, log=None, progress=None):
        self.debug_dir = Path(debug_dir or DEFAULT_DEBUG_DIR)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.headless = DEFAULT_HEADLESS if headless is None else headless
        self._log = log or (lambda msg: None)
        # 进度回调：progress(stage, pct, detail)。pct 为 None 表示该阶段无确定百分比
        self._progress_fn = progress or (lambda *a: None)

        self.pw = None
        self.browser = None
        self.context = None
        self.scan_page = None   # 二维码轮询页（后台）
        self.page = None        # 成绩查询工作页
        self._captured = []     # 截获的 JSON 响应
        self.qr_bytes = None
        self.last_excluded = []  # 最近一次抓取被剔除的课程（如体育）

    def _progress(self, stage, pct=None, detail=""):
        try:
            self._progress_fn(stage, pct, detail)
        except Exception:
            pass

    # ---------------- 基础启动 ---------------- #
    async def _launch_browser(self, headless, args):
        """按优先级启动 Chromium：JW_CHROMIUM 显式指定 > Playwright 自带 > 系统候选。

        自带浏览器与 Playwright 版本严格配套，必须优先；系统 Edge/Chrome 仅在
        自带浏览器缺失时兜底（如打包成 exe 的精简环境），避免驱动不配套浏览器。
        """
        attempts = []
        forced = os.environ.get("JW_CHROMIUM", "")
        if forced and Path(forced).exists():
            attempts.append((f"JW_CHROMIUM 指定的浏览器 {forced}",
                             {"executable_path": forced}))
        try:
            bundled = self.pw.chromium.executable_path
        except Exception:
            bundled = None
        if bundled and Path(bundled).exists():
            attempts.append(("Playwright 自带 Chromium", {}))
        attempts.extend(
            (f"系统浏览器 {p}", {"executable_path": p})
            for p in CHROMIUM_CANDIDATES if p and p != forced and Path(p).exists()
        )
        last_err = None
        for label, extra in attempts:
            try:
                self._log(f"使用 {label}")
                return await self.pw.chromium.launch(headless=headless, args=args, **extra)
            except Exception as e:
                last_err = e
                self._log(f"{label} 启动失败（{e.__class__.__name__}），尝试下一个候选…")
        try:
            await self.pw.stop()
        except Exception:
            pass
        raise RuntimeError(
            f"Chromium 启动失败（{last_err.__class__.__name__}）。请确认已执行 "
            "`python -m playwright install chromium`（Linux 加 --with-deps），"
            "或用 JW_CHROMIUM 指向系统浏览器；也可设置 ZC_ENGINE=http 使用纯 HTTP 引擎。"
        )

    async def start(self):
        self.pw = await async_playwright().start()
        # 无头（服务器）模式叠加一组省内存/去后台活动的参数；
        # 刻意不加 --single-process（Playwright 不支持，渲染器崩溃即整体失败）。
        headless_args = [
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu", "--no-zygote",
            "--disable-background-networking", "--disable-default-apps",
            "--disable-extensions", "--disable-sync", "--disable-translate",
            "--disable-component-update", "--disable-component-extensions-with-background-pages",
            "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding", "--metrics-recording-only", "--mute-audio",
            "--disable-features=TranslateUI,BackForwardCache,AcceptCHFrame,MediaRouter,"
            "OptimizationHints,InterestFeedContentSuggestions",
        ]
        headed_args = [
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        launch_kwargs = {"headless": self.headless,
                         "args": headless_args if self.headless else headed_args}
        self.browser = await self._launch_browser(**launch_kwargs)
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        # 全站 XHR 监听（所有 frame 的响应都会冒泡到 page）
        self.context.on("response", self._on_response)

    async def close(self):
        for obj in ("browser",):
            try:
                await getattr(self, obj).close()
            except Exception:
                pass
        try:
            await self.pw.stop()
        except Exception:
            pass

    async def logout(self):
        """清除登录 cookie 与页面状态；保留浏览器进程，下次扫码无需重启 Chromium。"""
        for attr in ("scan_page", "page"):
            pg = getattr(self, attr, None)
            if pg is None:
                continue
            try:
                # 关页前清掉该来源的 localStorage/sessionStorage（cookie 之外的登录态兜底）
                await pg.evaluate(
                    "try{localStorage.clear();sessionStorage.clear()}catch(e){}"
                )
            except Exception:
                pass
            try:
                await pg.close()
            except Exception:
                pass
        self.scan_page = None
        self.page = None
        self.qr_bytes = None
        self._captured = []
        if self.context is not None:
            try:
                await self.context.clear_cookies()
            except Exception as e:
                self._log(f"清除 cookie 失败（忽略）：{e}")
        self._log("已清除登录 cookie 与页面状态")

    async def health(self):
        """引擎是否仍连接可用（app.py 自愈逻辑调用）。"""
        try:
            return self.browser is not None and self.browser.is_connected()
        except Exception:
            return False

    async def _on_response(self, response):
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            # 只捕获教务主机（成绩接口都在这里）的 JSON：passport2 的扫码轮询
            # 等外部 JSON 不入内存，长会话不再缓慢累积；成绩行识别不受影响
            if urlsplit(response.url).netloc != BASE_HOST:
                return
            body = await response.text()
            # 硬上限兜底（不截断 body，避免破坏成绩 JSON 解析）
            if len(self._captured) >= 2000:
                self._captured.pop(0)
            self._captured.append({
                "url": response.url,
                "status": response.status,
                "body": body,
                # jqGrid 的筛选条件走 POST body（URL 上只有 fxbz/gridtype），
                # 记下来才能核对“学年学期”有没有真的传过去
                "post": _safe_post_data(response),
            })
        except Exception:
            pass

    # ---------------- 扫码登录（二维码内嵌到综测页面） ---------------- #
    async def open_scan_page(self):
        """在后台打开官方二维码页，并取出二维码图片字节。"""
        self.scan_page = await self.context.new_page()
        await self.scan_page.goto(SCAN_URL, wait_until="domcontentloaded", timeout=60000)
        await self.scan_page.wait_for_timeout(1500)

        info = await self._scan_dom_info()
        qr_src = info.get("qr")
        if not qr_src:
            raise RuntimeError("二维码页面未返回二维码地址")
        if qr_src.startswith("/"):
            qr_src = PASSPORT + qr_src
        resp = await self.context.request.get(
            qr_src, headers={"Referer": SCAN_URL, "Accept": "image/avif,image/webp,image/png,*/*"}
        )
        if resp.status != 200 or "image" not in resp.headers.get("content-type", ""):
            raise RuntimeError(
                f"二维码图片拉取失败（HTTP {resp.status}）。"
                "可能是当前网络/IP 被 passport2.chaoxing.com 限制，请改用“独立窗口扫码”模式。"
            )
        self.qr_bytes = await resp.body()
        self._log("二维码已生成，等待手机扫码…")
        return info

    async def _scan_dom_info(self):
        return await self.scan_page.evaluate(
            """() => {
                const q = s => document.querySelector(s);
                const shown = el => el && getComputedStyle(el).display !== 'none';
                return {
                    uuid: q('#uuid') ? q('#uuid').value : '',
                    enc:  q('#enc')  ? q('#enc').value  : '',
                    qr:   q('#ewm')  ? q('#ewm').src    : '',
                    nick: q('#shownickname') ? q('#shownickname').innerText.trim() : '',
                    confirmShown: shown(q('.g_confirm')),
                    expired: shown(q('.ewmDisable')),
                    url: location.href,
                };
            }"""
        )

    async def scan_state(self):
        """
        返回二维码登录状态：
          waiting   等待扫码
          scanned   已扫码，等待手机点确认
          expired   二维码已失效
          confirmed 登录成功，教务会话已建立
        """
        if self.scan_page is None:
            return {"state": "idle"}
        try:
            url = self.scan_page.url
            if "cloudscanlogin" not in url:
                # 页面已跳转到 /admin/scanLogin → /admin/...，会话建立
                ok = await self._verify_session()
                if ok:
                    self._log("扫码登录成功")
                    return {"state": "confirmed", "url": url}
            info = await self._scan_dom_info()
            if info.get("expired"):
                return {"state": "expired"}
            if info.get("confirmShown"):
                return {"state": "scanned", "nick": info.get("nick", "")}
            return {"state": "waiting"}
        except Exception as e:
            return {"state": "error", "msg": str(e)}

    async def refresh_scan(self):
        """二维码失效后重新加载。"""
        await self.scan_page.reload(wait_until="domcontentloaded", timeout=60000)
        await self.scan_page.wait_for_timeout(1200)
        info = await self._scan_dom_info()
        qr_src = info.get("qr", "")
        if qr_src.startswith("/"):
            qr_src = PASSPORT + qr_src
        resp = await self.context.request.get(qr_src, headers={"Referer": SCAN_URL})
        self.qr_bytes = await resp.body() if resp.status == 200 else None
        return info

    # ---------------- 扫码登录（独立浏览器窗口兜底） ---------------- #
    async def start_headed_window(self):
        """关闭无头上下文，弹出可见浏览器打开官方登录页，用户直接在窗口里扫码。"""
        try:
            await self.browser.close()
        except Exception:
            pass
        self.headless = False
        self.browser = await self._launch_browser(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        self.context = await self.browser.new_context(viewport={"width": 1100, "height": 760})
        self.context.on("response", self._on_response)
        win = await self.context.new_page()
        await win.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        self.scan_page = win
        self._log("已弹出登录窗口，请在窗口中用学习通 App 扫码…")

    async def wait_headed_login(self, timeout=300):
        deadline = time.time() + timeout
        while time.time() < deadline:
            url = self.scan_page.url
            if "/admin/login" not in url and "cloudscanlogin" not in url:
                await self.scan_page.wait_for_timeout(2000)
                if await self._verify_session():
                    self._log("窗口扫码登录成功")
                    return True
            await asyncio.sleep(1.5)
        return False

    # ---------------- 会话校验 & 定位成绩 frame ---------------- #
    async def _verify_session(self):
        """带当前 Cookie 请求成绩页，若不被踢回登录页则会话有效。"""
        try:
            resp = await self.context.request.get(GRADES_URL)
            final = resp.url
            if "/admin/login" in final:
                return False
            return resp.status == 200
        except Exception:
            return False

    async def _open_work_page(self):
        if self.page is not None:
            return
        self.page = await self.context.new_page()
        await self.page.goto(GRADES_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await self.page.wait_for_timeout(1500)
        await self._open_grades_tab()

    def _has_grades_frame(self):
        """是否已出现「全部成绩查询」内容页（URL 含 qbcjcx）。"""
        try:
            return any("qbcjcx" in (f.url or "") for f in self.page.frames)
        except Exception:
            return False

    async def _open_grades_tab(self):
        """
        切到「全部成绩查询」页签。
        直接访问 /admin/indexMain/M1402 打开的是「信息查询」门户：默认 iframe 是
        学生卡片（xskp），其内嵌「最高成绩查询」（zgcjcx）——关键词打分会误选它；
        必须点左侧菜单切到真正的全部成绩查询（/admin//xsd/xsdcjcx/qbcjcx）。
        """
        if self._has_grades_frame():
            return
        clicked = False
        for _ in range(6):                      # 菜单脚本可能稍晚才渲染
            try:
                for f in await self._all_frames():
                    link = await f.query_selector(f"a:has-text('{GRADES_MENU_TEXT}')")
                    if link is None:
                        continue
                    # 用 JS click：菜单项可能处于折叠/隐藏态，可见性等待会 30s 超时
                    await link.evaluate("el => el.click()")
                    self._log(f"点击菜单「{GRADES_MENU_TEXT}」")
                    clicked = True
                    break
            except Exception as e:
                self._log(f"点击菜单失败：{e}")
                break
            if clicked:
                break
            await asyncio.sleep(0.5)
        if clicked:
            for _ in range(24):                 # 最多等 12s 让内容 iframe 加载
                if self._has_grades_frame():
                    return
                await asyncio.sleep(0.5)
        if not self._has_grades_frame():
            self._log("菜单未命中，直接打开全部成绩查询页…")
            for path in GRADES_CONTENT_PATHS:
                try:
                    resp = await self.page.goto(
                        BASE + path, wait_until="domcontentloaded", timeout=60000
                    )
                    if resp is not None and resp.status >= 400:
                        continue
                    await self.page.wait_for_timeout(1500)
                    break
                except Exception as e:
                    self._log(f"直接打开失败：{e}")

    async def _all_frames(self):
        """工作页的全部 frame（排除二维码后台页）。"""
        frames = []
        for f in self.page.frames:
            try:
                if self.scan_page and f == self.scan_page.main_frame:
                    continue
            except Exception:
                pass
            frames.append(f)
        return frames

    async def _grades_frame(self):
        """
        遍历 frame，按“成绩/学分/课程名关键词 + select 数量”打分，
        返回最像“全部成绩查询”的 frame。
        """
        await self._open_work_page()
        best, best_score = None, -1
        for f in await self._all_frames():
            try:
                txt = await f.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                continue
            score = 0
            for kw, w in [("成绩", 3), ("学分", 2), ("课程名", 2), ("课程号", 2),
                          ("查询", 1), ("学年", 1), ("学期", 1)]:
                score += txt.count(kw) * w
            try:
                score += len(await f.query_selector_all("select")) * 2
                score += len(await f.query_selector_all("table")) * 2
            except Exception:
                pass
            url = f.url or ""
            if "qbcjcx" in url:
                score += 200          # 命中「全部成绩查询」内容页
            elif "zgcjcx" in url or "studentScoreInquiry" in url:
                score -= 60           # 「最高成绩查询」：关键词多但不是目标页
            elif "xskp" in url:
                score -= 60           # 「学生卡片」门户页
            if score > best_score:
                best, best_score = f, score
        if best is None or best_score <= 0:
            raise RuntimeError("未能在任何 frame 中定位成绩查询区域")
        self._log(f"定位成绩查询 frame（得分 {best_score}）：{best.url}")
        return best

    # ---------------- 读取学年/学期真实选项 ---------------- #
    async def discover_filters(self):
        frame = await self._grades_frame()
        selects = await frame.query_selector_all("select")
        items = []
        for s in selects:
            opts = await s.evaluate(
                "el => [...el.options].map(o => ({value:o.value, text:o.innerText.trim()}))"
            )
            label = await s.evaluate(
                """el => {
                    let n = el.closest('tr,li,form,div');
                    return n ? n.innerText.replace(/\\s+/g,' ').slice(0,200) : '';
                }"""
            )
            items.append({
                "id": await s.get_attribute("id"),
                "name": await s.get_attribute("name"),
                "label": label,
                "options": opts,
            })

        def is_semester_opt(o):
            """选项形如 2025-2026-2（学年学期合并值）。"""
            return bool(re.match(r"^\s*\d{4}\s*[-/]\s*\d{4}\s*[-/]\s*\d+\s*$", o["text"] or ""))

        def is_semester_select(it):
            opts = it["options"]
            if not opts:
                return False
            hit = sum(1 for o in opts if is_semester_opt(o))
            return hit >= max(1, len(opts) // 2)

        def is_year(it):
            if "学年" in it["label"]:
                return True
            return any(re.search(r"\d{4}\s*[-/]\s*\d{4}", o["text"]) for o in it["options"])

        def is_term(it):
            if "学期" in it["label"]:
                return True
            texts = [o["text"] for o in it["options"]]
            return len(texts) >= 2 and all(
                re.fullmatch(r"\s*([123]|[一二三四]|第?[一二三四123]学期?)\s*", t) for t in texts if t
            )

        # 截图所示「全部成绩查询」页只有“起始学年学期 / 终止学年学期”两个区间下拉，
        # 没有独立的学年、学期下拉 → 用这两个下拉组成区间（起始 = 终止 = 所选学期）
        sem_items = [it for it in items if is_semester_select(it)]
        semester_pair = len(sem_items) >= 2
        if semester_pair:
            first = sem_items[0]
            second = next((it for it in sem_items if it is not first), sem_items[-1])
            start = next((it for it in sem_items if "起始" in it["label"]), first)
            end = next((it for it in sem_items if "终止" in it["label"]), second)
            if end is start:
                end = second if start is first else first
            year, term = start, end
        else:
            year = next((it for it in items if is_year(it)), None)
            term = next((it for it in items if is_term(it) and it is not year), None)

        def with_all(it):
            if it is None:
                return None
            has_all = any(("全部" in o["text"] or o["value"] == "") for o in it["options"])
            opts = list(it["options"])
            if not has_all:
                opts.insert(0, {"value": "__ALL__", "text": "全部"})
            return {**it, "options": opts}

        self._log(
            f"读取筛选条件：学年下拉 {'有' if year else '无'}，学期下拉 {'有' if term else '无'}"
            + ("（起止学年学期区间模式）" if semester_pair else "")
        )
        return {
            "year": with_all(year),
            "term": with_all(term),
            "semester_pair": semester_pair,
            "all_selects": items,
        }

    # ---------------- 设置筛选 + 点击查询 ---------------- #
    @staticmethod
    def _split_code_name(v):
        """“[CS1001]程序设计基础” → ('CS1001', '程序设计基础')；拆不开时 code 为 None。"""
        s = str(v or "").strip()
        m = re.match(r"^\s*[\[【]\s*([A-Za-z0-9\-_.]+)\s*[\]】]\s*(.+)$", s)
        if m:
            return m.group(1), m.group(2).strip()
        return None, s

    @staticmethod
    def _split_year_term(v):
        """“2025-2026-2” → ('2025-2026', '2')；不是合并值则返回 (None, None)。"""
        s = str(v or "").strip()
        m = re.match(r"^\s*(\d{4}\s*[-/]\s*\d{4})\s*[-/_]\s*(\d+)\s*$", s)
        if not m:
            return None, None
        return re.sub(r"\s+", "", m.group(1)), m.group(2)

    @staticmethod
    def _semester_values(item):
        """取“起始/终止学年学期”下拉里形如 2025-2026-2 的真实选项值。"""
        return [
            o["value"]
            for o in (item or {}).get("options", [])
            if re.match(r"^\s*\d{4}\s*[-/]\s*\d{4}\s*[-/]\s*\d+\s*$", o.get("text") or "")
        ]

    @staticmethod
    def _has_all_option(item):
        """页面是否自带“入学以来 / 全部”选项（value=001 或文本含入学以来/全部）。"""
        for o in (item or {}).get("options", []):
            v = str(o.get("value") or "")
            t = str(o.get("text") or "")
            if v == SEMESTER_ALL_VALUE or "入学以来" in t or "全部" in t:
                return True
        return False

    def _semester_range(self, year_item, term_item, year_value, term_value):
        """
        截图所示页面用“起始学年学期 / 终止学年学期”两个区间下拉：
          - 起始、终止都选了具体学期 → 取小者为起始、大者为终止；
          - 只选了一个具体学期     → 起止都设成该学期；
          - 选“全部”              → 用页面自带的“入学以来”(value=001)，
                                    没有该选项时退回 最早..最晚。
        """
        vals = sorted(set(self._semester_values(year_item) + self._semester_values(term_item)))

        def specific(v):
            return v if v and v not in ("__ALL__", SEMESTER_ALL_VALUE) and v in vals else None

        sv, tv = specific(year_value), specific(term_value)
        if sv and tv:
            return (sv, tv) if sv <= tv else (tv, sv)
        if sv or tv:
            one = sv or tv
            return one, one
        if self._has_all_option(year_item) or self._has_all_option(term_item):
            return SEMESTER_ALL_VALUE, SEMESTER_ALL_VALUE
        if vals:
            return vals[0], vals[-1]
        return "__ALL__", "__ALL__"

    # ---------------- 本机二次过滤（保证约束一定生效） ---------------- #
    @staticmethod
    def _row_semester(r):
        """把规范化行还原成页面里的学年学期取值，如 2025-2026-2。"""
        y, t = r.get("year"), r.get("term")
        if y and t:
            return f"{y}-{t}"
        if y and re.match(r"^\d{4}-\d{4}-\d+$", str(y)):
            return str(y)
        return ""

    def _filter_semesters(self, rows, start_v, end_v):
        """
        按用户所选学年学期在本机再过滤一次。
        教务页面的筛选偶尔不生效（例如查询请求仍带着下拉默认值），
        只靠页面过滤会把全部学期的成绩都抓回来，所以这里做硬校验。
        """
        if not start_v or start_v == SEMESTER_ALL_VALUE:
            return rows, []
        lo, hi = (start_v, end_v) if str(start_v) <= str(end_v) else (end_v, start_v)
        keep, drop, unknown = [], [], []
        for r in rows:
            s = self._row_semester(r)
            if not s:
                unknown.append(r)
            elif lo <= s <= hi:
                keep.append(r)
            else:
                drop.append(r)
        if drop or unknown:
            self._log(
                f"按学年学期 {lo}~{hi} 过滤：保留 {len(keep)} 门，"
                f"剔除 {len(drop)} 门其它学期"
                + (f"、{len(unknown)} 门无学期信息" if unknown else "")
            )
        return keep, drop + unknown

    def _exclude_courses(self, rows):
        """剔除不纳入专业成绩的课程（默认：体育）。"""
        keep, dropped = [], []
        for r in rows:
            name = str(r.get("name") or "")
            hit = next((kw for kw in EXCLUDE_COURSE_KEYWORDS if kw and kw in name), None)
            (dropped if hit else keep).append(r)
        if dropped:
            desc = "、".join(
                f'{r.get("name")}（{r.get("score_raw") or "-"}，{r.get("credit") or "-"}学分）'
                for r in dropped
            )
            self._log(f"已剔除不纳入专业成绩的课程：{desc}")
        return keep, dropped

    # ---------------- 查询请求参数核对 ---------------- #
    def _grid_request_params(self):
        """从截获的 jqGrid 请求里取出筛选参数（POST body + URL）。"""
        out = []
        for cap in self._captured:
            url = str(cap.get("url") or "")
            if GRID_API_KEY not in url:
                continue
            params = {}
            for chunk in re.split(r"[&\n]", str(cap.get("post") or "")):
                if "=" in chunk:
                    k, _, v = chunk.partition("=")
                    params[k.strip()] = unquote_plus(v.strip())
            for k in ("xnxq", "xnxq2"):
                if k in params:
                    continue
                m = re.search(rf"[?&]{k}=([^&]*)", url)
                if m:
                    params[k] = unquote_plus(m.group(1))
            if params:
                out.append(params)
        return out

    def _log_grid_request_params(self):
        """打印最近一次查询请求里的学年学期参数，便于核对筛选是否真的生效。"""
        reqs = self._grid_request_params()
        if not reqs:
            self._log("未截获成绩接口请求（可能被页面缓存命中），已用本机校验兜底")
            return {}
        got = {k: v for k, v in reqs[-1].items() if k in ("xnxq", "xnxq2")}
        if got:
            self._log(f"查询请求的学年学期参数：{got}")
        else:
            self._log("查询请求未带学年学期参数（接口用会话默认值），已用本机校验兜底")
        return got

    async def _select_one(self, frame, item, value):
        if not item:
            return
        sel = None
        if item.get("id"):
            sel = await frame.query_selector(f"select#{item['id']}")
        if sel is None and item.get("name"):
            sel = await frame.query_selector(f"select[name='{item['name']}']")
        if sel is None:
            self._log(f"未找到下拉：{item.get('label')}")
            return
        # 用 JS 读真实 DOM 选项并改值：Playwright select_option 对不存在的选项
        # （如前端注入的合成“全部” value=__ALL__）会各等 30s 超时，两次 60s
        # 正是此前抓取静默 62 秒后崩溃的原因。
        try:
            chosen = await sel.evaluate(
                """(el, req) => {
                    const opts = [...el.options];
                    let opt = null;
                    if (req.value === '__ALL__') {
                        opt = opts.find(o => (o.textContent || '').indexOf('全部') >= 0)
                           || opts.find(o => o.value === '');
                        if (!opt) return '';   // 没有“全部”就维持页面默认
                    } else {
                        opt = opts.find(o => o.value === req.value);
                        if (!opt) return '';
                    }
                    el.value = opt.value;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    return (opt.textContent || '').trim() || opt.value;
                }""",
                {"value": value},
            )
        except Exception as e:
            self._log(f"设置下拉失败（忽略，继续抓取）：{e}")
            return
        if chosen:
            self._log(f"已选择：{chosen}")
        else:
            self._log(f"保持默认：{item.get('label')}")

    @staticmethod
    async def _try_click(btn):
        """
        先常规点击，失败后回退 DOM 点击。
        教务页面里“搜索”按钮常被悬浮的查询条/固定工具栏遮住（Playwright 要求
        元素中心可用指针命中），常规点击会 5s 超时；而 DOM click 直接触发
        onclick 里的 search()，不受遮挡与可见性影响。
        """
        try:
            await btn.click(timeout=2500)
            return "click"
        except Exception:
            pass
        try:
            await btn.evaluate("el => el.click()")
            return "dom"
        except Exception:
            return None

    async def _click_query(self, frame):
        """
        点“搜索”按钮。真实页面标记：
          <button class="btn btn-sm btn-info"
                  onclick="search('xsdcjcxGridIdGrid')"><i class="fa "></i> 搜索</button>
        """
        selectors = []
        for text in QUERY_BUTTON_TEXTS:
            selectors += [
                f"button:has-text('{text}')",
                f"a:has-text('{text}')",
                f"input[value*='{text}']",
            ]
        # 兜底：任意调用 search(...) 的按钮 / jqGrid 工具栏按钮
        selectors += ["button[onclick*='search(']", ".pull-right button.btn-info"]

        for sel in selectors:
            try:
                btn = await frame.query_selector(sel)
            except Exception:
                continue
            if btn is None:
                continue
            try:
                label = ((await btn.inner_text()) or "").strip() or sel
            except Exception:
                label = sel
            how = await self._try_click(btn)
            if how:
                self._log(f"点击“{label}”" + ("（DOM 点击）" if how == "dom" else ""))
                return True
            self._log(f"点击失败，跳过：{sel}")

        # 兜底：回车触发。注意 Frame 没有 keyboard 属性，必须用 Page 的键盘
        self._log("未找到可点击的搜索按钮，尝试回车触发")
        try:
            await self.page.keyboard.press("Enter")
        except Exception as e:
            self._log(f"回车兜底失败：{e}")
        return False

    # ---------------- jqGrid 数据渲染等待 / 分页探测 ---------------- #
    async def _grid_row_count(self, frame):
        try:
            return await frame.evaluate(
                """() => {
                    const t = document.querySelector('table.ui-jqgrid-btable');
                    if (!t) return 0;
                    return [...t.querySelectorAll('tr')]
                        .filter(tr => tr.querySelector('td[aria-describedby]')).length;
                }"""
            )
        except Exception:
            return 0

    async def _wait_grid_data(self, frame, timeout=25):
        """等 jqGrid 数据行渲染出来（点“搜索”后异步渲染）。"""
        deadline = time.time() + timeout
        n = 0
        while time.time() < deadline:
            n = await self._grid_row_count(frame)
            if n:
                return n
            await asyncio.sleep(0.8)
        return n

    async def _grid_fingerprint(self, frame):
        """
        表格内容指纹（行数 + 每行 id/文本前缀）。
        只判断“有没有行”是不够的：上一轮查询的行还留在 DOM 里，
        会把旧结果当成新结果读进来，所以必须等指纹变化。
        """
        try:
            return await frame.evaluate(
                """() => {
                    const t = document.querySelector('table.ui-jqgrid-btable');
                    if (!t) return '';
                    const rows = [...t.querySelectorAll('tr')]
                        .filter(tr => tr.querySelector('td[aria-describedby]'));
                    if (!rows.length) return '';
                    return rows.length + '|' + rows.map(
                        tr => tr.id + ':' + (tr.innerText || '').replace(/\\s+/g, '').slice(0, 40)
                    ).join(',');
                }"""
            )
        except Exception:
            return ""

    async def _wait_grid_change(self, frame, before, timeout=25):
        """等表格换成“本次搜索”的结果（指纹变化且非空）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            fp = await self._grid_fingerprint(frame)
            if fp and fp != before:
                await asyncio.sleep(0.4)          # 等渲染稳定
                return await self._grid_row_count(frame)
            await asyncio.sleep(0.6)
        n = await self._grid_row_count(frame)
        if n:
            self._log("数据与上一轮一致（筛选条件未变），按现有行继续")
        return n

    async def _grid_page_no(self, frame):
        try:
            return await frame.evaluate(
                """() => {
                    const el = document.querySelector('.ui-jqgrid-pager input.ui-pg-input');
                    const n = el ? parseInt(el.value, 10) : NaN;
                    return isNaN(n) ? 1 : n;
                }"""
            )
        except Exception:
            return 1

    async def _wait_page_change(self, frame, before, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self._grid_page_no(frame) != before:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _wait_results(self, frame, timeout=25):
        """等表格出现数据行或网络静默。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                txt = await frame.evaluate("() => document.body ? document.body.innerText : ''")
                if re.search(r"\d", txt) and ("学分" in txt or "成绩" in txt):
                    tables = await frame.query_selector_all("table")
                    for t in tables:
                        rows = await t.query_selector_all("tr")
                        if len(rows) >= 2:
                            return
            except Exception:
                pass
            await asyncio.sleep(0.8)
        try:
            await frame.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    # ---------------- XHR 成绩行提取 ---------------- #
    def _rows_from_captured(self):
        rows = []
        for cap in self._captured:
            try:
                data = json.loads(cap["body"])
            except Exception:
                continue
            self._collect_rows(data, rows, cap["url"])
        return rows

    def _collect_rows(self, obj, out, url):
        if isinstance(obj, dict):
            mapped = {}
            for k, v in obj.items():
                c = _map_field(k)
                if c and not isinstance(v, (dict, list)):
                    mapped[c] = v
            if mapped.get("name") or mapped.get("code"):
                if mapped.get("score") is not None or mapped.get("credit") is not None:
                    mapped["_src"] = "xhr"
                    out.append(mapped)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    self._collect_rows(v, out, url)
        elif isinstance(obj, list):
            for v in obj:
                if isinstance(v, (dict, list)):
                    self._collect_rows(v, out, url)

    # ---------------- DOM 表格提取 ---------------- #
    async def _rows_from_dom(self):
        """
        读取页面渲染出来的成绩表格。
        「全部成绩查询」页用的是 jqGrid：**表头和数据体是两张不同的 table**
        （表头 ui-jqgrid-htable，数据体 ui-jqgrid-btable），数据格靠
        aria-describedby="<gridId>_<列名>" 关联表头 id="jqgh_<gridId>_<列名>"。
        所以这里用 JS 一次性按“列名→中文表头”取数，再统一映射成规范字段；
        同时兼容表头在第一行的普通表格。
        """
        js = r"""
        () => {
          const out = [];
          const push = o => { if (o && Object.keys(o).length > 1) out.push(o); };

          // 1) jqGrid：表头表 + 数据体表
          document.querySelectorAll('table.ui-jqgrid-btable').forEach(t => {
            const gid = t.id;
            if (!gid) return;
            const head = {};
            document.querySelectorAll('th[id^="jqgh_' + gid + '_"], #gbox_' + gid + ' .ui-jqgrid-htable th').forEach(th => {
              const col = (th.id || '').replace('jqgh_' + gid + '_', '');
              const txt = (th.innerText || '').trim();
              if (col) head[col] = txt || col;
            });
            t.querySelectorAll('tr').forEach(tr => {
              const tds = tr.querySelectorAll('td[aria-describedby]');
              if (!tds.length) return;
              const o = { __src: 'dom' };
              tds.forEach(td => {
                const ab = td.getAttribute('aria-describedby') || '';
                const col = ab.indexOf(gid + '_') === 0 ? ab.slice(gid.length + 1) : ab;
                const key = head[col] || col;
                const val = (td.innerText || '').trim();
                if (val) o[key] = val;
              });
              push(o);
            });
          });

          // 2) 普通表格：表头在第一行
          document.querySelectorAll('table').forEach(t => {
            const cls = (t.className || '').toString();
            if (cls.indexOf('jqgrid') >= 0) return;
            const trs = [...t.querySelectorAll('tr')];
            if (trs.length < 2) return;
            const heads = [...trs[0].querySelectorAll('th,td')].map(c => (c.innerText || '').trim());
            if (!heads.filter(h => h).length) return;
            trs.slice(1).forEach(tr => {
              const cells = [...tr.querySelectorAll('td')];
              if (!cells.length) return;
              const o = { __src: 'dom' };
              cells.forEach((td, i) => {
                const val = (td.innerText || '').trim();
                const key = heads[i];
                if (val && key) o[key] = val;
              });
              push(o);
            });
          });
          return out;
        }
        """
        rows = []
        for f in await self._all_frames():
            try:
                data = await f.evaluate(js)
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for r in data:
                if not isinstance(r, dict):
                    continue
                mapped = {"_src": "dom"}
                for k, v in r.items():
                    if k == "__src" or v in (None, ""):
                        continue
                    canon = _map_field(k)
                    if canon:
                        mapped.setdefault(canon, v)   # 同名列取最左侧出现的那个
                if mapped.get("name") or mapped.get("code"):
                    rows.append(mapped)
        return rows

    # ---------------- 分页 ---------------- #
    async def _maximize_page_size(self, frame):
        """把 layui / jqGrid 分页组件的每页条数调到最大（jqGrid 是 select.ui-pg-selbox）。"""
        for css in (
            "select.ui-pg-selbox",
            ".ui-jqgrid-pager select",
            ".layui-laypage-limits select",
        ):
            try:
                sel = await frame.query_selector(css)
            except Exception:
                sel = None
            if sel is None:
                continue
            try:
                picked = await sel.evaluate(
                    """el => {
                        const vals = [...el.options]
                            .map(o => o.value)
                            .filter(v => v !== '' && !isNaN(Number(v)));
                        if (!vals.length) return '';
                        const best = vals.sort((a, b) => Number(b) - Number(a))[0];
                        if (el.value === best) return '';
                        el.value = best;
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return best;
                    }"""
                )
                if picked:
                    self._log(f"每页条数调至 {picked}")
                    await frame.wait_for_timeout(2000)
                    return
            except Exception as e:
                self._log(f"调整每页条数失败（忽略）：{e}")

    async def _goto_next_page(self, frame):
        before = await self._grid_page_no(frame)
        for sel in [
            ".layui-laypage-next:not(.layui-disabled)",
            "a.layui-laypage-next:not(.layui-disabled)",
            # jqGrid 翻页按钮 id 形如 next_<gridId>，禁用时带 ui-state-disabled
            "td[id^='next_']:not(.ui-state-disabled)",
            ".ui-jqgrid-pager td[id^='next']:not(.ui-state-disabled)",
        ]:
            btn = await frame.query_selector(sel)
            if btn is None:
                continue
            if await self._try_click(btn) and await self._wait_page_change(frame, before):
                return True
        # 通用“下一页 / 追加下一页”
        btns = await frame.query_selector_all("a,button,td.ui-pg-button,span")
        for b in btns:
            try:
                t = (await b.inner_text()).strip()
                cls = await b.get_attribute("class") or ""
                if t in ("下一页", "下页", "追加下一页", "»", ">") and "disabled" not in cls:
                    if await self._try_click(b) and await self._wait_page_change(frame, before):
                        return True
            except Exception:
                continue
        return False

    # ---------------- 合并归一化 ---------------- #
    @staticmethod
    def _normalize_row(r):
        # 成绩：综合成绩优先，缺失时用分项成绩
        score = r.get("score")
        if score in (None, ""):
            score = r.get("score_item")
        # 学分：课程学分优先，缺失时用获得学分
        credit = r.get("credit")
        if credit in (None, ""):
            credit = r.get("credit_earned")

        code = (str(r.get("code") or "")).strip() or None
        name = (str(r.get("name") or "")).strip() or None
        if name and name[0] in "[【":
            c2, n2 = GradeCrawler._split_code_name(name)
            if c2:
                code = code or c2
                name = n2
        if not name and code:
            name = code

        year = (str(r.get("year") or "")).strip() or None
        term = (str(r.get("term") or "")).strip() or None
        y2, t2 = GradeCrawler._split_year_term(r.get("year") or "")
        if y2 is None:
            y2, t2 = GradeCrawler._split_year_term(r.get("term") or "")
        if y2:
            year, term = y2, t2

        return {
            "code": code,
            "name": name,
            "credit": _to_float(credit),
            "score_raw": (str(score).strip() if score not in (None, "") else None) or None,
            "score": _to_float(score),
            "nature": (str(r.get("nature") or "")).strip() or None,
            "gpa": _to_float(r.get("gpa")),
            "teacher": (str(r.get("teacher") or "")).strip() or None,
            "year": year,
            "term": term,
            "classname": (str(r.get("classname") or "")).strip() or None,
            "sources": [r.get("_src", "?")],
        }

    @staticmethod
    def _key(r):
        term = r.get("term") or ""
        term = re.sub(r"\D", "", term) or term
        return (r.get("code") or "", r.get("name") or "", r.get("year") or "", term)

    def _merge(self, raw_rows):
        merged = {}
        for r in raw_rows:
            n = self._normalize_row(r)
            k = self._key(n)
            if k not in merged:
                merged[k] = n
                continue
            old = merged[k]
            for field, val in n.items():
                if field == "sources":
                    old["sources"] = sorted(set(old["sources"] + val))
                elif old.get(field) in (None, "") and val not in (None, ""):
                    old[field] = val
        return list(merged.values())

    # ---------------- 对外主流程 ---------------- #
    async def crawl(self, year_value="__ALL__", term_value="__ALL__", filters=None):
        self._progress("打开成绩页", 4)
        frame = await self._grades_frame()
        self._captured = []  # 只统计本次查询产生的 XHR

        if filters:
            year_item, term_item = filters.get("year"), filters.get("term")
            if filters.get("semester_pair"):
                # 截图所示页面：起始学年学期 / 终止学年学期 区间下拉
                start_v, end_v = self._semester_range(year_item, term_item, year_value, term_value)
                self._log(f"学年学期区间：起始 {start_v} → 终止 {end_v}")
                await self._select_one(frame, year_item, start_v)
                await self._select_one(frame, term_item, end_v)
            else:
                start_v = end_v = year_value if year_value != "__ALL__" else term_value
                await self._select_one(frame, year_item, year_value)
                await self._select_one(frame, term_item, term_value)

        self._progress("提交查询", 15, f"{start_v}~{end_v}")
        await self._maximize_page_size(frame)
        before_fp = await self._grid_fingerprint(frame)
        await self._click_query(frame)
        got = await self._wait_grid_change(frame, before_fp, timeout=25)
        if not got:
            # 有些页面第一次搜索只刷新表格框架，需要再触发一次
            self._log("首次未取到数据，重新触发一次搜索…")
            before_fp = await self._grid_fingerprint(frame)
            await self._click_query(frame)
            got = await self._wait_grid_change(frame, before_fp, timeout=20)
        if got:
            self._log(f"表格数据行 {got} 条")
        else:
            await self._wait_results(frame, timeout=10)   # 非 jqGrid 页面兜底

        raw = []
        page_no = 1
        self._progress("抓取成绩", 22)
        while True:
            raw.extend(self._rows_from_captured())
            raw.extend(await self._rows_from_dom())
            self._log(f"第 {page_no} 页：累计原始记录 {len(raw)} 条")
            # 浏览器模式拿不到总页数，进度随页数爬升、封顶 82%
            self._progress(
                "抓取成绩", min(82, 22 + page_no * 9),
                f"第 {page_no} 页 · 累计 {len(raw)} 条",
            )
            if not await self._goto_next_page(frame):
                break
            page_no += 1
            if page_no > 100:
                break

        self._log_grid_request_params()          # 核对请求里带的学年学期参数

        rows = self._merge(raw)
        self._log(f"合并去重后共 {len(rows)} 门课程")
        self._progress("解析合并", 90, f"共 {len(rows)} 门课程")
        if not rows:
            await self.dump("empty_result")
            raise RuntimeError(
                "未解析到任何成绩。已将页面快照保存到 debug/ 目录，"
                "请把该目录发回以便适配（也可先确认所选学年学期是否有成绩）。"
            )

        # 硬校验：即使教务页面忽略了筛选条件，也保证只返回用户所选学年学期
        rows, dropped = self._filter_semesters(rows, start_v, end_v)
        if not rows:
            await self.dump("empty_after_filter")
            raise RuntimeError(
                f"所选学年学期（{start_v}~{end_v}）下没有查到成绩。"
                "页面抓回的记录都不在该区间，已保存快照到 debug/ 目录。"
            )

        # 规则剔除：体育等不纳入专业学习成绩的课程
        rows, excluded = self._exclude_courses(rows)
        self.last_excluded = excluded
        self._log(
            f"最终 {len(rows)} 门纳入专业成绩"
            + (f"，另剔除 {len(excluded)} 门（不纳入专业成绩）" if excluded else "")
        )
        self._progress("完成", 100, f"{len(rows)} 门课程")

        rows.sort(key=lambda r: (r.get("year") or "", r.get("term") or "", r.get("code") or ""))
        return rows

    # ---------------- 诊断快照 ---------------- #
    def _prune_debug(self):
        """只保留最近 DEBUG_KEEP 个快照目录，避免服务器上无限增长。"""
        keep = DEBUG_KEEP
        if keep <= 0:
            return
        try:
            dirs = [p for p in self.debug_dir.iterdir() if p.is_dir()]
            dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            return
        for old in dirs[keep:]:
            try:
                shutil.rmtree(old, ignore_errors=True)
                self._log(f"清理旧快照：{old.name}")
            except Exception:
                pass

    async def dump(self, tag="error"):
        if not DEBUG_WRITE:
            self._log("诊断落盘已关闭（ZC_DEBUG_WRITE=0），跳过快照")
            return ""
        ts = time.strftime("%Y%m%d_%H%M%S")
        d = self.debug_dir / f"{tag}_{ts}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            if self.page:
                await self.page.screenshot(path=str(d / "workpage.png"), full_page=True)
                for i, f in enumerate(await self._all_frames()):
                    try:
                        (d / f"frame_{i}.html").write_text(
                            _scrub(await f.content()), encoding="utf-8"
                        )
                    except Exception:
                        pass
            if self.scan_page:
                await self.scan_page.screenshot(path=str(d / "scanpage.png"))
        except Exception as e:
            (d / "dump_error.txt").write_text(str(e), encoding="utf-8")
        try:
            # 脱敏后再落盘：快照常被发回排查，别把学号/姓名一起带出去
            safe = [{**cap, "body": _scrub(cap.get("body"))} for cap in self._captured]
            (d / "captured.json").write_text(
                json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        self._prune_debug()
        self._log(f"诊断快照已保存（已脱敏）：{d}")
        return str(d)
#（注：内容由AI生成）
