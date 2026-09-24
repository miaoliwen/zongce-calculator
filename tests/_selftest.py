# -*- coding: utf-8 -*-
"""
按截图（jqGrid「全部成绩查询」页）真实结构做的端到端自测：
  1) 列名映射        2) 归一化（拆课程号 / 拆学年学期 / 成绩·学分回退）
  3) 起止学年学期区间 4) 真实标记下跑 crawl()（含被遮挡按钮的 DOM 点击回退）
  5) 多页拼接        6) 回归：不得再出现 frame.keyboard 之类会崩的属性
运行：python -m tests._selftest   （在仓库根目录执行；详细结果见 tests/_selftest_out.txt）
"""
import asyncio
import inspect
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from crawler import GradeCrawler, _map_field  # noqa: E402

LOG = open(HERE / "_selftest_out.txt", "w", encoding="utf-8")
FAILS = []
LINES = []


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


def weighted(rows):
    c = sum(r["credit"] for r in rows)
    return round(sum(r["score"] * r["credit"] for r in rows) / c, 3), c


# ---------- 1) 截图里出现的真实列名/字段 → 规范字段 ----------
log("=== 1) 列名映射 ===")
for h, want in [
    ("学年学期", "year"), ("课程/环节", "name"), ("课程学分", "credit"),
    ("课程性质", "nature"), ("课程属性", "nature"), ("课程类别", "nature"),
    ("分项成绩", "score_item"), ("综合成绩", "score"), ("绩点", "gpa"),
    ("获得学分", "credit_earned"),
]:
    check(f"map({h})", _map_field(h), want)

# ---------- 2) 归一化 ----------
log("\n=== 2) 归一化（_normalize_row 收到的是已映射的规范键）===")
n = GradeCrawler._normalize_row({
    "_src": "dom", "year": "2025-2026-2", "name": "[CS1001]程序设计基础",
    "score": "82", "credit_earned": "4", "credit": "",
})
log("row ->", n)
check("code", n["code"], "CS1001")
check("name", n["name"], "程序设计基础")
check("score(综合成绩)", n["score"], 82.0)
check("credit(回退获得学分)", n["credit"], 4.0)
check("year(拆开)", n["year"], "2025-2026")
check("term(拆开)", n["term"], "2")

n2 = GradeCrawler._normalize_row({"_src": "dom", "name": "体育", "score": "",
                                  "score_item": "85.5", "credit": "2", "credit_earned": "2"})
check("综合成绩空→分项成绩", n2["score"], 85.5)
check("课程学分优先", n2["credit"], 2.0)

# 接口原始字段 → 规范键（DOM 与 XHR 两条路都靠它）
for k, want in [("kcmc", "name"), ("xnxq", "year"), ("zhcj", "score"),
                ("fxcj", "score_item"), ("xf", "credit"), ("hdxf", "credit_earned")]:
    check(f"map(字段 {k})", _map_field(k), want)

# ---------- 3) 起止学年学期区间 ----------
log("\n=== 3) 起止学年学期区间 ===")
c = GradeCrawler(debug_dir=str(HERE / "debug"))
item = {"label": "起始学年学期", "options": [
    {"value": "2025-2026-2", "text": "2025-2026-2"},
    {"value": "2025-2026-1", "text": "2025-2026-1"},
    {"value": "2024-2025-2", "text": "2024-2025-2"},
]}
check("无“入学以来”时退回最早..最晚", c._semester_range(item, item, "__ALL__", "__ALL__"),
      ("2024-2025-2", "2025-2026-2"))
item_all = {"label": "起始学年学期", "options": [{"value": "001", "text": "入学以来"}] + item["options"]}
check("有“入学以来”时用它（全部）", c._semester_range(item_all, item_all, "__ALL__", "__ALL__"),
      ("001", "001"))
check("指定学期→起止相同", c._semester_range(item, item, "2025-2026-1", "2025-2026-1"),
      ("2025-2026-1", "2025-2026-1"))
check("两个学期→小的当起始", c._semester_range(item, item, "2025-2026-2", "2025-2026-1"),
      ("2025-2026-1", "2025-2026-2"))

# ---------- 3b) 本机二次过滤 / 体育剔除 ----------
log("\n=== 3b) 本机过滤与剔除 ===")
_r = lambda y, n, s, cr: {"year": y, "term": "1", "code": n, "name": n,
                          "score": s, "credit": cr, "score_raw": str(s), "sources": ["dom"]}
sample = [_r("2025-2026", "A", 90, 2), _r("2024-2025", "B", 80, 3), _r("2025-2026", "体育", 86, 1)]
kept, drop = c._filter_semesters(sample, "2025-2026-1", "2025-2026-1")
check("只留所选学期", [x["name"] for x in kept], ["A", "体育"])
check("其它学期被剔除", [x["name"] for x in drop], ["B"])
kept2, excl = c._exclude_courses(kept)
check("体育被剔除", [x["name"] for x in excl], ["体育"])
check("其余保留", [x["name"] for x in kept2], ["A"])
check("全部时不本地过滤", len(c._filter_semesters(sample, "001", "001")[0]), 3)
check("截获请求参数解析", callable(c._grid_request_params), True)

# ---------- 6) 代码级回归 ----------
log("\n=== 6) 回归检查 ===")
src = inspect.getsource(GradeCrawler)
check("不再使用 frame.keyboard", "frame.keyboard" in src, False)
check("回车兜底改用 page.keyboard", "self.page.keyboard" in src, True)
check("存在 DOM 点击回退", "el => el.click()" in src, True)


async def e2e():
    crawler = GradeCrawler(debug_dir=str(HERE / "debug"),
                           log=lambda m: (LINES.append(m), log("  ·", m)))
    await crawler.start()
    try:
        # ---------- 4) 真实标记 + 被悬浮条遮挡的搜索按钮 ----------
        log("\n=== 4) 端到端：真实按钮标记（被悬浮条遮挡）===")
        crawler.page = await crawler.context.new_page()
        await crawler.page.goto((HERE / "_selftest_fixture.html").as_uri(),
                                wait_until="domcontentloaded")
        await crawler.page.wait_for_timeout(300)

        filters = await crawler.discover_filters()
        check("识别为起止学年学期区间模式", filters.get("semester_pair"), True)
        check("起始下拉", (filters.get("year") or {}).get("id"), "startXnxq")
        check("终止下拉", (filters.get("term") or {}).get("id"), "endXnxq")

        # 回归前提：按钮确实被悬浮条遮住（常规点击必然超时）
        covered = await crawler.page.evaluate(
            """() => {
                const b = document.querySelector("button[onclick*='search(']");
                const r = b.getBoundingClientRect();
                const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return !!el && el !== b && !b.contains(el);
            }"""
        )
        check("搜索按钮被悬浮条遮挡", covered, True)

        rows = await crawler.crawl("__ALL__", "__ALL__", filters)
        check("全部学期课程数（已剔除体育）", len(rows), 11)
        check("被遮挡的按钮仍点到了（onclick 生效）", await crawler.page.evaluate(
            "() => document.body.getAttribute('data-searched')"), "1")
        check("走的是 DOM 点击回退", any("（DOM 点击）" in x for x in LINES), True)
        check("没有误点“设置”", await crawler.page.evaluate(
            "() => document.body.getAttribute('data-setting')"), None)
        check("全部时用“入学以来”", await crawler.page.evaluate(
            "() => document.body.getAttribute('data-range')"), "001..001")
        check("剔除项=体育", [r["name"] for r in crawler.last_excluded], ["体育"])
        check("剔除项带成绩学分", crawler.last_excluded[0]["credit"], 2.0)
        check("行里没有体育",
              [r["name"] for r in rows if "体育" in (r["name"] or "")], [])
        check("每页条数调到最大", await crawler.page.evaluate(
            "() => document.querySelector('select.ui-pg-selbox').value"), "500")
        avg, tot = weighted(rows)
        check("全部 Σ学分（不含体育）", tot, 36.0)
        check("全部 加权平均", avg, 81.264)
        check("来源", sorted({s for r in rows for s in r["sources"]}), ["dom"])
        for r in sorted(rows, key=lambda x: (x["year"], x["term"], x["code"])):
            log("  row ->", r["year"], r["term"], r["code"], r["name"], r["score"], r["credit"])

        # 指定学期（2025-2026-2）
        rows2 = await crawler.crawl("2025-2026-2", "2025-2026-2", filters)
        check("指定学期课程数（已剔除体育）", len(rows2), 7)
        check("起止区间=同一学期", await crawler.page.evaluate(
            "() => document.body.getAttribute('data-range')"), "2025-2026-2..2025-2026-2")
        check("指定学期只含该学期",
              sorted({f'{r["year"]}-{r["term"]}' for r in rows2}), ["2025-2026-2"])
        avg2, tot2 = weighted(rows2)
        check("该学期 Σ学分", tot2, 24.0)
        check("该学期 加权平均", avg2, 81.792)

        # ---------- 4b) 页面忽略筛选条件时，本机硬校验仍生效 ----------
        log("\n=== 4b) 页面忽略筛选（#nofilter）===")
        nf = await crawler.context.new_page()
        await nf.goto((HERE / "_selftest_fixture.html").as_uri() + "#nofilter",
                      wait_until="domcontentloaded")
        crawler.page = nf
        await crawler.page.wait_for_timeout(300)
        f3 = await crawler.discover_filters()
        rows_nf = await crawler.crawl("2025-2026-2", "2025-2026-2", f3)
        check("页面返回全部 12 条，本机仍筛到 7 门",
              len(rows_nf), 7)
        check("筛出来的都属于所选学期",
              sorted({f'{r["year"]}-{r["term"]}' for r in rows_nf}), ["2025-2026-2"])
        check("忽略筛选时本机也剔除了体育",
              [r["name"] for r in rows_nf if "体育" in (r["name"] or "")], [])

        # ---------- 5) 多页拼接（每页最多 10 条 → 12 条 2 页） ----------
        log("\n=== 5) 多页拼接（#size=5,10）===")
        paged = await crawler.context.new_page()
        await paged.goto((HERE / "_selftest_fixture.html").as_uri() + "#size=5,10",
                         wait_until="domcontentloaded")
        crawler.page = paged
        await crawler.page.wait_for_timeout(300)
        f2 = await crawler.discover_filters()
        rows3 = await crawler.crawl("__ALL__", "__ALL__", f2)
        check("翻页拼出全部（剔除体育后 11 条）", len(rows3), 11)
        check("停在最后 1 页", await crawler.page.evaluate(
            "() => document.querySelector('.ui-jqgrid-pager input.ui-pg-input').value"), "2")
    finally:
        await crawler.close()


try:
    asyncio.run(e2e())
except Exception:  # noqa: BLE001
    import traceback
    FAILS.append("exception")
    log("EXCEPTION:\n" + traceback.format_exc())

log("\n=== 结论：" + ("ALL TESTS PASSED" if not FAILS else f"{len(FAILS)} FAILED -> {FAILS}") + " ===")
LOG.close()
print("ALL TESTS PASSED" if not FAILS else f"FAILED: {FAILS}  (see _selftest_out.txt)")